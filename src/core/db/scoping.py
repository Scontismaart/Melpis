"""Primitive di tenant scoping (Invariante #1: Tenant Isolation).

Unica tabella di verita' per le tabelle tenant-scoped: il check runtime
(ScopedConnection) e lo script CI condividono le stesse costanti.

Fail-closed ovunque:
- organizzazione mancante/non valida -> MissingOrganizationIdError/ValueError
- statement su tabella tenant-scoped senza un predicato esplicito
  organization_id = bind nella WHERE, oppure senza organization_id nelle
  colonne di INSERT -> TenantScopeViolation.

Il controllo riconosce la forma sintattica, non dimostra che il valore bind
provenga dall'organizzazione autenticata. Quella corrispondenza resta compito
dell'autorizzazione applicativa.

Le tabelle in INDIRECT_SCOPED_TABLES sono escluse dal check diretto:
non portano organization_id propria ma restano coperte da RLS e dal
filtro sulla tabella padre (es. message_delivery_attempts via messages).
"""
import re
import uuid
from contextlib import asynccontextmanager
from typing import NamedTuple

# Scope inventory for every table created by the SQL migrations and canonical
# bootstrap schemas. Each entry is (classification, rationale):
#   direct          carries its own organization_id and must be tenant-filtered;
#   parent-derived  has no organization_id and is scoped through a tenant parent;
#   system/root     identity or organization root data, not tenant child data;
#   platform/infra  platform-wide queue/idempotency data, not tenant reads.
TABLE_SCOPE_CLASSIFICATION: dict[str, tuple[str, str]] = {
    "gdpr_operations": ("direct", "Durable rights request is owned by a single organization."),
    "gdpr_export_chunks": ("direct", "Encrypted snapshot chunks are bound to the operation and organization."),
    "gdpr_operation_steps": ("direct", "Provider/local progress is bound to a tenant operation."),
    "gdpr_operation_events": ("direct", "Append-only rights evidence is tenant scoped."),
    "gdpr_policies": ("direct", "Versioned controller approvals are bound to one tenant."),
    "gdpr_erasure_barriers": ("direct", "Erasure tombstones fence data production within a tenant."),
    "organizations": ("system/root", "Tenant root rows; selected by organization primary key."),
    "user_profiles": ("system/root", "User principal profile keyed by auth user, may belong to multiple organizations."),
    "auth_access_lifecycle": ("system/root", "Server-only durable principal access and provisioning history keyed by immutable auth identity."),
    "organization_memberships": ("direct", "Membership is owned by exactly one organization."),
    "audit_log": ("direct", "Each audit event carries its organization_id."),
    "processed_stripe_events": ("platform/infra", "Stripe event idempotency ledger; organization_id is optional and event processing is platform-wide."),
    "webhook_idempotency": ("platform/infra", "Provider event deduplication keys are platform-wide before tenant resolution."),
    "whatsapp_accounts": ("direct", "WhatsApp account belongs to one organization."),
    "contacts": ("direct", "Contact identity is isolated per organization."),
    "conversations": ("direct", "Conversation carries its organization_id."),
    "messages": ("direct", "Message carries its organization_id."),
    "message_delivery_attempts": ("parent-derived", "Delivery attempt belongs to a message; resolve organization through messages."),
    "contact_consent_log": ("parent-derived", "Consent event belongs to a contact; resolve organization through contacts."),
    "whatsapp_templates": ("direct", "Template belongs to one organization."),
    "bookings": ("direct", "Booking carries its organization_id."),
    "booking_confirmation_outbox": ("direct", "Confirmation send is bound to one organization's booking and messages."),
    "booking_settings": ("direct", "Settings row is keyed by organization_id."),
    "reviews": ("direct", "Review carries its organization_id."),
    "documents": ("direct", "Document metadata carries its organization_id."),
    "document_chunks": ("direct", "Chunk carries organization_id and has a consistency trigger against its document."),
    "email_configs": ("direct", "Email configuration belongs to one organization."),
    "usage_events": ("direct", "Usage event is attributed to one organization."),
    "provider_attempt_runtime": ("direct", "Provider worker lease and fencing state is bound to one organization."),
    "provider_attempt_evidence": ("direct", "Append-only provider attempt evidence carries its organization_id."),
    "event_log": ("direct", "Event projection carries its organization_id."),
    "simulation_requests": ("direct", "Idempotent simulation request is keyed by organization and user."),
    "google_calendar_credentials": ("direct", "Calendar credentials are owned by one organization."),
    "oauth_nonces": ("direct", "OAuth nonce is bound to one organization for the authorization flow."),
    "google_business_credentials": ("direct", "Google Business credentials are owned by one organization."),
    "onboarding_profiles": ("direct", "Onboarding profile is owned by one organization."),
    "instagram_accounts": ("direct", "Instagram account is owned by one organization."),
    "faq_cache": ("direct", "Cached answer is attributed to one organization."),
    "message_feedback": ("direct", "Feedback row carries organization_id."),
    "weekly_report_log": ("direct", "Report run belongs to one organization."),
    "outbound_dedup": ("direct", "Outbound idempotency record is keyed within one organization."),
    "external_booking_credentials": ("direct", "External booking credentials are keyed by organization."),
    "external_booking_sync": ("direct", "Sync record and idempotency key are organization-scoped."),
    "airtable_connections": ("direct", "Airtable connection belongs to one organization."),
    "airtable_field_mappings": ("direct", "Field mapping belongs to one organization."),
    "airtable_webhooks": ("direct", "Webhook registration belongs to one organization."),
    "airtable_webhook_events": ("direct", "Webhook event ledger is keyed by organization and webhook."),
    "meta_webhook_inbox": ("direct", "Accepted webhook event is assigned to one organization before persistence."),
    "billing_checkout_intents": ("direct", "Checkout retry intent is keyed by organization and request key."),
    "governance_outbox": ("direct", "Governance event is attributed to one organization."),
    "team_invitations": ("direct", "Pending invitation grant belongs to one organization."),
}

_CLASSIFIED_DIRECT = frozenset(
    table for table, (kind, _reason) in TABLE_SCOPE_CLASSIFICATION.items()
    if kind == "direct"
)
TENANT_SCOPED_TABLES: frozenset[str] = _CLASSIFIED_DIRECT

INDIRECT_SCOPED_TABLES: frozenset[str] = frozenset(
    table for table, (kind, _reason) in TABLE_SCOPE_CLASSIFICATION.items()
    if kind == "parent-derived"
)

_PARENT_DERIVED_GUARDED_TABLES = frozenset({
    "message_delivery_attempts", "contact_consent_log",
})
_PARENT_RELATIONS = {
    "message_delivery_attempts": ("messages", "message_id"),
    "contact_consent_log": ("contacts", "contact_id"),
}


class _SqlToken(NamedTuple):
    kind: str
    value: str
    depth: int


def _is_keyword(token: _SqlToken, value: str) -> bool:
    """Quoted identifiers may spell keywords but never act as SQL syntax."""
    return token.kind == "IDENT" and token.value == value


def _is_identifier(token: _SqlToken) -> bool:
    return token.kind in {"IDENT", "QIDENT"}


class _TableReference(NamedTuple):
    table: str
    alias: str
    operation: str
    token_index: int
    end_index: int
    depth: int


class _QueryBlock(NamedTuple):
    kind: str
    token_index: int
    end_index: int
    depth: int


_RELATION_OPERATIONS = frozenset({"from", "join", "update", "into", "using", "copy", "truncate"})
_DDL_TABLE_OPERATIONS = frozenset({"alter", "create", "drop", "lock"})
_SUPPORTED_STATEMENT_STARTS = frozenset({"select", "insert", "update", "delete", "with"})
_NON_DML_OPERATIONS = frozenset({
    "alter", "call", "comment", "copy", "create", "do", "drop", "grant",
    "lock", "refresh", "reindex", "revoke", "truncate", "vacuum",
})
_CLAUSE_TERMINATORS = frozenset({
    "returning", "order", "group", "having", "limit", "offset", "fetch",
    "for", "window", "union", "except", "intersect",
})
_NON_ALIAS_IDENTIFIERS = frozenset({
    "where", "join", "inner", "left", "right", "full", "cross", "on",
    "using", "natural", "outer", "set", "values", "returning", "order",
    "group", "having", "limit", "offset", "fetch", "for", "window",
    "union", "except", "intersect", "when", "then", "else", "end",
    "as", "only", "lateral",
})


def _tokenize_sql(sql: str) -> list[_SqlToken]:
    """Tokenize the small SQL subset needed by the fail-closed scope guard.

    Strings and comments are opaque. Quoted identifiers remain identifiers,
    including schema-qualified and quoted table names. This is not a complete
    PostgreSQL parser; unsupported shapes are rejected by the scope analysis.
    """
    tokens: list[_SqlToken] = []
    index = 0
    depth = 0
    while index < len(sql):
        char = sql[index]
        following = sql[index:index + 2]
        if char.isspace():
            index += 1
            continue
        if following == "--":
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline < 0 else newline + 1
            continue
        if following == "/*":
            comment_depth = 1
            index += 2
            while index < len(sql) and comment_depth:
                if sql[index:index + 2] == "/*":
                    comment_depth += 1
                    index += 2
                elif sql[index:index + 2] == "*/":
                    comment_depth -= 1
                    index += 2
                else:
                    index += 1
            if comment_depth:
                raise ValueError("unterminated SQL comment")
            continue
        if char == "'":
            index += 1
            while index < len(sql):
                if sql[index] == "'":
                    if index + 1 < len(sql) and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                if sql[index] == "\\" and index + 1 < len(sql):
                    index += 2
                else:
                    index += 1
            else:
                raise ValueError("unterminated SQL string")
            tokens.append(_SqlToken("STRING", "", depth))
            continue
        if char == '"':
            index += 1
            identifier = []
            while index < len(sql):
                if sql[index] == '"':
                    if index + 1 < len(sql) and sql[index + 1] == '"':
                        identifier.append('"')
                        index += 2
                        continue
                    index += 1
                    break
                identifier.append(sql[index])
                index += 1
            else:
                raise ValueError("unterminated quoted SQL identifier")
            tokens.append(_SqlToken("QIDENT", "".join(identifier), depth))
            continue
        if char == "$":
            parameter = re.match(r"\$(\d+)", sql[index:])
            if parameter:
                tokens.append(_SqlToken("PARAM", parameter.group(1), depth))
                index += len(parameter.group(0))
                continue
            delimiter = re.match(r"\$(?:[a-zA-Z_][a-zA-Z0-9_]*)?\$", sql[index:])
            if delimiter:
                marker = delimiter.group(0)
                end = sql.find(marker, index + len(marker))
                if end < 0:
                    raise ValueError("unterminated dollar-quoted SQL string")
                index = end + len(marker)
                tokens.append(_SqlToken("STRING", "", depth))
                continue
        if char.isalpha() or char == "_":
            word_match = re.match(r"[^\W\d][\w$]*", sql[index:], flags=re.UNICODE)
            if word_match:
                word = word_match.group(0)
                tokens.append(_SqlToken("IDENT", word.lower(), depth))
                index += len(word)
            else:
                # Non-ASCII alphabetic code points that aren't valid identifier
                # starts remain opaque symbols instead of crashing the scanner.
                tokens.append(_SqlToken("SYMBOL", char, depth))
                index += 1
            continue
        if char == "(":
            tokens.append(_SqlToken("SYMBOL", char, depth))
            depth += 1
            index += 1
            continue
        if char == ")":
            if depth == 0:
                raise ValueError("unbalanced SQL parentheses")
            depth -= 1
            tokens.append(_SqlToken("SYMBOL", char, depth))
            index += 1
            continue
        if following in {"::", "<=", ">=", "<>", "!=", "||", "=>"}:
            tokens.append(_SqlToken("SYMBOL", following, depth))
            index += 2
            continue
        tokens.append(_SqlToken("SYMBOL", char, depth))
        index += 1
    if depth:
        raise ValueError("unbalanced SQL parentheses")
    return tokens


def _statement_start_indexes(tokens: list[_SqlToken]) -> list[int]:
    starts = []
    at_start = True
    for index, token in enumerate(tokens):
        if token.depth != 0:
            continue
        if token.value == ";":
            at_start = True
        elif at_start:
            starts.append(index)
            at_start = False
    return starts


def _statement_starts(tokens: list[_SqlToken]) -> list[str]:
    return [tokens[index].value for index in _statement_start_indexes(tokens)]


def has_only_dml_statements(sql: str) -> bool:
    """Return false when any statement begins with a non-DML/unsupported verb."""
    tokens = _tokenize_sql(sql)
    return all(
        tokens[index].kind == "IDENT" and tokens[index].value in _SUPPORTED_STATEMENT_STARTS
        for index in _statement_start_indexes(tokens)
    )


def has_non_dml_operation(sql: str) -> bool:
    """Detect known DDL/administrative verbs for the static CI scanner."""
    tokens = _tokenize_sql(sql)
    starts = _statement_start_indexes(tokens)
    for ordinal, start_index in enumerate(starts):
        if tokens[start_index].kind != "IDENT":
            continue
        operation = tokens[start_index].value
        if operation not in _NON_DML_OPERATIONS:
            continue
        end_index = starts[ordinal + 1] if ordinal + 1 < len(starts) else len(tokens)
        words = [
            token.value for token in tokens[start_index + 1:end_index]
            if token.depth == 0 and token.kind == "IDENT"
        ]
        if operation in {"alter", "create", "drop"}:
            position = 0
            if operation == "create" and words[:2] == ["or", "replace"]:
                position = 2
            if position < len(words) and words[position] == "materialized":
                position += 1
            if position < len(words) and words[position] in {
                "table", "index", "schema", "database", "view", "function",
                "procedure", "trigger", "type", "sequence", "role",
            }:
                return True
        elif operation == "truncate":
            if words and words[0] == "table":
                words = words[1:]
            if words and re.match(r"[a-z_]", words[0]):
                return True
        elif operation == "copy":
            if words and re.match(r"[a-z_]", words[0]):
                return True
        elif operation == "lock":
            if words and words[0] == "table":
                words = words[1:]
            if words and re.match(r"[a-z_]", words[0]):
                return True
        elif operation in {"grant", "revoke"}:
            if "on" in words:
                on_index = words.index("on")
                if on_index + 1 < len(words) and words[on_index + 1] in {
                    "table", "sequence", "schema", "database", "function",
                    "procedure", "type",
                }:
                    return True
        elif operation == "comment":
            if words[:1] == ["on"] and len(words) > 1:
                return True
        elif operation == "refresh":
            if words[:1] == ["materialized"]:
                return True
        elif operation == "reindex":
            if words[:1] in (["index"], ["table"], ["database"], ["schema"], ["system"]):
                return True
        elif operation == "call":
            if words and re.match(r"[a-z_]", words[0]) and "(" in words:
                return True
        elif operation == "do":
            if words and words[0] == "":
                return True
        elif operation == "vacuum":
            if not words or words[0] in {"analyze", "full", "freeze", "verbose"}:
                return True
    return False


def _relation_after(tokens: list[_SqlToken], keyword_index: int) -> tuple[str, str, int] | None:
    index = keyword_index + 1
    if _is_keyword(tokens[keyword_index], "truncate") and index < len(tokens) and _is_keyword(tokens[index], "table"):
        index += 1
    if (
        _is_keyword(tokens[keyword_index], "table")
        and index + 1 < len(tokens)
        and _is_keyword(tokens[index], "if")
        and any(_is_keyword(tokens[index + 1], value) for value in {"exists", "not"})
    ):
        index += 2
        if _is_keyword(tokens[index - 1], "not") and index < len(tokens) and _is_keyword(tokens[index], "exists"):
            index += 1
    while index < len(tokens) and any(_is_keyword(tokens[index], value) for value in {"only", "lateral"}):
        index += 1
    wrapped_only = index < len(tokens) and tokens[index].value == "("
    if wrapped_only:
        index += 1
    if index >= len(tokens) or not _is_identifier(tokens[index]):
        return None
    names = [tokens[index].value]
    index += 1
    while index + 1 < len(tokens) and tokens[index].value == "." and _is_identifier(tokens[index + 1]):
        names.append(tokens[index + 1].value)
        index += 2
    if wrapped_only:
        if index >= len(tokens) or tokens[index].value != ")":
            return None
        index += 1
    table = names[-1]
    alias = table
    if index < len(tokens) and _is_keyword(tokens[index], "as"):
        if index + 1 >= len(tokens) or not _is_identifier(tokens[index + 1]):
            return None
        alias = tokens[index + 1].value
        index += 2
    elif (
        index < len(tokens)
        and _is_identifier(tokens[index])
        and (tokens[index].kind == "QIDENT" or tokens[index].value not in _NON_ALIAS_IDENTIFIERS)
    ):
        alias = tokens[index].value
        index += 1
    return table, alias, index


def _table_references(tokens: list[_SqlToken]) -> list[_TableReference]:
    references = []
    for index, token in enumerate(tokens):
        operation = token.value
        is_ddl_table = (
            _is_keyword(token, "table")
            and index > 0
            and any(_is_keyword(tokens[index - 1], value) for value in _DDL_TABLE_OPERATIONS)
        )
        if not any(_is_keyword(token, value) for value in _RELATION_OPERATIONS) and not is_ddl_table:
            continue
        if is_ddl_table:
            operation = "ddl"
        if _is_keyword(token, "update") and index and any(
            _is_keyword(tokens[index - 1], value) for value in {"for", "do"}
        ):
            continue
        relation = _relation_after(tokens, index)
        if relation is None:
            continue
        table, alias, end = relation
        references.append(_TableReference(table, alias, operation, index + 1, end, token.depth))
    return references


def _query_blocks(tokens: list[_SqlToken]) -> list[_QueryBlock]:
    starts = []
    for index, token in enumerate(tokens):
        kind = None
        if _is_keyword(token, "select"):
            kind = "select"
        elif _is_keyword(token, "insert") and index + 1 < len(tokens) and _is_keyword(tokens[index + 1], "into"):
            kind = "insert"
        elif _is_keyword(token, "update") and (index == 0 or not any(
            _is_keyword(tokens[index - 1], value) for value in {"for", "do"}
        )):
            kind = "update"
        elif _is_keyword(token, "delete") and index + 1 < len(tokens) and _is_keyword(tokens[index + 1], "from"):
            kind = "delete"
        if kind:
            starts.append((index, kind, token.depth))

    start_depths = {start: depth for start, _kind, depth in starts}
    blocks = []
    for start, kind, depth in starts:
        end = len(tokens)
        for cursor in range(start + 1, len(tokens)):
            token = tokens[cursor]
            if token.depth < depth or (token.depth == depth and (
                token.value == ";" or any(_is_keyword(token, value) for value in {"union", "except", "intersect"})
            )):
                end = cursor
                break
            if token.depth == depth and cursor in start_depths:
                end = cursor
                break
        blocks.append(_QueryBlock(kind, start, end, depth))
    return blocks


def _block_references(
    block: _QueryBlock, references: list[_TableReference],
) -> list[_TableReference]:
    compatible = {
        "select": {"from", "join"},
        "update": {"update", "from", "join"},
        "delete": {"from", "using", "join"},
        "insert": {"into"},
    }[block.kind]
    return [
        ref for ref in references
        if ref.depth == block.depth
        and block.token_index < ref.token_index < block.end_index
        and ref.operation in compatible
    ]


def _where_tokens(tokens: list[_SqlToken], block: _QueryBlock) -> list[_SqlToken] | None:
    where_index = next(
        (index for index in range(block.token_index + 1, block.end_index)
         if tokens[index].depth == block.depth and _is_keyword(tokens[index], "where")),
        None,
    )
    if where_index is None:
        return None
    end = next(
        (index for index in range(where_index + 1, block.end_index)
         if tokens[index].depth == block.depth and any(
             _is_keyword(tokens[index], value) for value in _CLAUSE_TERMINATORS
         )),
        block.end_index,
    )
    return tokens[where_index + 1:end]


def _has_comma_join(tokens: list[_SqlToken], block: _QueryBlock) -> bool:
    """Return true for comma-separated relations, which this parser rejects.

    Commas in projections, VALUES, and nested expressions have a different
    depth or occur outside the FROM/USING relation list. The relation list
    continues through JOIN ... ON expressions: PostgreSQL permits a comma
    relation after an ON clause. Explicit JOIN remains supported and every
    joined tenant table is checked independently.
    """
    clause_ends = {
        "where", "set", "group", "having", "order", "limit",
        "offset", "fetch", "window", "for", "returning",
        "union", "except", "intersect",
    }
    for index in range(block.token_index + 1, block.end_index):
        token = tokens[index]
        if token.depth != block.depth or not any(_is_keyword(token, value) for value in {"from", "using"}):
            continue
        end = next(
            (cursor for cursor in range(index + 1, block.end_index)
             if tokens[cursor].depth == block.depth
             and any(_is_keyword(tokens[cursor], value) for value in clause_ends)),
            block.end_index,
        )
        if any(
            tokens[cursor].depth == block.depth and tokens[cursor].value == ","
            for cursor in range(index + 1, end)
        ):
            return True
    return False


def _join_on_tokens(
    tokens: list[_SqlToken], block: _QueryBlock, ref: _TableReference,
) -> list[_SqlToken] | None:
    if ref.operation not in {"join", "using"}:
        return None
    on_index = next(
        (index for index in range(ref.end_index, block.end_index)
         if tokens[index].depth == block.depth
         and any(_is_keyword(tokens[index], value) for value in {"on", "using", "join", "where"})),
        None,
    )
    if on_index is None or not _is_keyword(tokens[on_index], "on"):
        return None
    end = next(
        (index for index in range(on_index + 1, block.end_index)
         if tokens[index].depth == block.depth
         and any(_is_keyword(tokens[index], value) for value in {"join", "where", "group", "order", "limit", "returning"})),
        block.end_index,
    )
    return tokens[on_index + 1:end]


def _outer_join_kinds(tokens: list[_SqlToken], block: _QueryBlock) -> set[str]:
    kinds = set()
    for index in range(block.token_index, block.end_index):
        if tokens[index].depth != block.depth or not _is_keyword(tokens[index], "join"):
            continue
        modifier = index - 1
        if modifier >= block.token_index and _is_keyword(tokens[modifier], "outer"):
            modifier -= 1
        if modifier >= block.token_index:
            for kind in ("left", "right", "full"):
                if _is_keyword(tokens[modifier], kind):
                    kinds.add(kind)
    return kinds


def _scope_params_for_reference(
    tokens: list[_SqlToken], block: _QueryBlock, ref: _TableReference,
    where: list[_SqlToken] | None, allow_unqualified: bool,
) -> list[int] | None:
    scopes = []
    if where:
        scopes.append(where)
    on_clause = _join_on_tokens(tokens, block, ref)
    # LEFT JOIN's right-hand relation is filtered by its own ON. RIGHT/FULL
    # may preserve that relation (or an earlier join result): require WHERE
    # rather than attempting to prove scope through a mixed join tree.
    if on_clause and not (_outer_join_kinds(tokens, block) & {"right", "full"}):
        scopes.append(on_clause)
    params = []
    for expression in scopes:
        found = _scope_params_for_alias(
            expression, block.depth, ref.alias, allow_unqualified,
        )
        if found:
            params.extend(found)
    return params or None


def _strip_token_parentheses(
    expression: list[_SqlToken], base_depth: int,
) -> tuple[list[_SqlToken], int]:
    expression = list(expression)
    while (
        len(expression) >= 2
        and expression[0].value == "("
        and expression[-1].value == ")"
        and expression[0].depth == base_depth
        and expression[-1].depth == base_depth
    ):
        depth = 0
        wraps_all = True
        for index, token in enumerate(expression):
            if token.value == "(":
                depth += 1
            elif token.value == ")":
                depth -= 1
                if depth == 0 and index != len(expression) - 1:
                    wraps_all = False
                    break
        if not wraps_all or depth:
            break
        expression = expression[1:-1]
        base_depth += 1
    return expression, base_depth


def _org_bind_in_segment(
    segment: list[_SqlToken], base_depth: int, alias: str, allow_unqualified: bool,
) -> list[int]:
    segment, base_depth = _strip_token_parentheses(segment, base_depth)
    if any(_is_keyword(token, "or") for token in segment):
        return []

    # A scope must be the complete positive conjunct. Do not accept a bind
    # equality merely because it appears inside NOT, CASE, IS FALSE, a cast,
    # function, or another expression whose meaning this small parser cannot
    # prove. Parameter casts are supported only immediately after the bind.
    values = [token.value for token in segment]

    def parameter_at(index: int) -> int | None:
        if index < len(segment) and segment[index].kind == "PARAM":
            if values[index + 1:] in ([], ["::", "uuid"]):
                return int(segment[index].value)
        return None

    if (
        len(segment) >= 5
        and _is_identifier(segment[0]) and segment[0].value == alias
        and values[1:4] == [".", "organization_id", "="]
    ):
        parameter = parameter_at(4)
        return [parameter] if parameter is not None else []

    if allow_unqualified and len(segment) >= 3:
        if _is_identifier(segment[0]) and segment[0].value == "organization_id" and values[1] == "=":
            parameter = parameter_at(2)
            return [parameter] if parameter is not None else []

    if segment and segment[0].kind == "PARAM":
        equals_index = 1
        if values[1:3] == ["::", "uuid"]:
            equals_index = 3
        if equals_index < len(segment) and values[equals_index] == "=":
            right = values[equals_index + 1:]
            if right == [alias, ".", "organization_id"]:
                return [int(segment[0].value)]
            if allow_unqualified and right == ["organization_id"]:
                return [int(segment[0].value)]
    return []


def _scope_params_for_alias(
    where: list[_SqlToken] | None,
    base_depth: int,
    alias: str,
    allow_unqualified: bool,
) -> list[int] | None:
    if not where:
        return None
    where, base_depth = _strip_token_parentheses(where, base_depth)
    if any(_is_keyword(token, "or") and token.depth == base_depth for token in where):
        return None
    segments: list[list[_SqlToken]] = []
    start = 0
    for index, token in enumerate(where):
        if _is_keyword(token, "and") and token.depth == base_depth:
            segments.append(where[start:index])
            start = index + 1
    segments.append(where[start:])
    safe_params = []
    for segment in segments:
        safe_params.extend(_org_bind_in_segment(segment, base_depth, alias, allow_unqualified))
    return safe_params or None


def _insert_scope_params(
    tokens: list[_SqlToken], block: _QueryBlock, ref: _TableReference,
) -> list[int] | None:
    depth = block.depth
    target = _insert_target_columns(tokens, block, ref)
    if target is None:
        return None
    columns, close_index = target
    if "organization_id" not in columns:
        return None
    org_position = columns.index("organization_id")
    values_index = next(
        (index for index in range(close_index + 1, block.end_index)
         if tokens[index].depth == depth and _is_keyword(tokens[index], "values")),
        None,
    )
    if values_index is None:
        return None
    params = []
    index = values_index + 1
    while index < block.end_index:
        if tokens[index].depth != depth or tokens[index].value != "(":
            break
        row_open = index
        row_close = next(
            (cursor for cursor in range(row_open + 1, block.end_index)
             if tokens[cursor].depth == depth and tokens[cursor].value == ")"),
            None,
        )
        if row_close is None:
            return None
        values: list[list[_SqlToken]] = []
        start = row_open + 1
        for cursor in range(row_open + 1, row_close):
            if tokens[cursor].value == "," and tokens[cursor].depth == depth + 1:
                values.append(tokens[start:cursor])
                start = cursor + 1
        values.append(tokens[start:row_close])
        if org_position >= len(values):
            return None
        cell = values[org_position]
        params_in_cell = [token for token in cell if token.kind == "PARAM"]
        other = [
            token for token in cell
            if _is_identifier(token) and token.value not in {"uuid"}
        ]
        if len(params_in_cell) != 1 or other:
            return None
        allowed_cell = [token.value for token in cell]
        if allowed_cell not in ([f"{params_in_cell[0].value}"], [f"{params_in_cell[0].value}", "::", "uuid"]):
            return None
        params.append(int(params_in_cell[0].value))
        index = row_close + 1
        if index < block.end_index and tokens[index].value == "," and tokens[index].depth == depth:
            index += 1
            continue
        break
    return params or None


def _insert_target_columns(
    tokens: list[_SqlToken], block: _QueryBlock, ref: _TableReference,
) -> tuple[list[str], int] | None:
    depth = block.depth
    relation_index = ref.token_index
    # The target column list is the first same-depth parenthesized list after
    # the target relation; aliases are allowed but other INSERT shapes fail closed.
    open_index = next(
        (index for index in range(relation_index + 1, block.end_index)
         if tokens[index].depth == depth and tokens[index].value == "("),
        None,
    )
    if open_index is None:
        return None
    close_index = next(
        (index for index in range(open_index + 1, block.end_index)
         if tokens[index].depth == depth and tokens[index].value == ")"),
        None,
    )
    if close_index is None:
        return None
    columns = [
        token.value for token in tokens[open_index + 1:close_index]
        if token.depth == depth + 1 and _is_identifier(token)
    ]
    return columns, close_index


def _upsert_conflict_target_is_scoped(
    tokens: list[_SqlToken],
    block: _QueryBlock,
    required_column: str,
    target_alias: str,
) -> bool:
    """Require tenant identity in the key or an equivalent guarded update."""
    conflict_index = next(
        (index for index in range(block.token_index, block.end_index - 1)
         if tokens[index].depth == block.depth
         and _is_keyword(tokens[index], "on")
         and tokens[index + 1].depth == block.depth
         and _is_keyword(tokens[index + 1], "conflict")),
        None,
    )
    if conflict_index is None:
        return True
    do_index = next(
        (index for index in range(conflict_index + 2, block.end_index - 1)
         if tokens[index].depth == block.depth and _is_keyword(tokens[index], "do")),
        None,
    )
    if do_index is None:
        return False
    if _is_keyword(tokens[do_index + 1], "nothing"):
        return True
    if not _is_keyword(tokens[do_index + 1], "update"):
        return False

    # Tenant identity (or a parent-derived ownership link) is immutable even
    # when the conflict target itself includes that column.
    set_index = next(
        (index for index in range(do_index + 2, block.end_index)
         if tokens[index].depth == block.depth and _is_keyword(tokens[index], "set")),
        None,
    )
    if set_index is None:
        return False
    set_end = next(
        (index for index in range(set_index + 1, block.end_index)
         if tokens[index].depth == block.depth
         and any(_is_keyword(tokens[index], value) for value in {"where", "returning"})),
        block.end_index,
    )
    start = set_index + 1
    assignments = []
    for index in range(start, set_end):
        if tokens[index].depth == block.depth and tokens[index].value == ",":
            assignments.append(tokens[start:index])
            start = index + 1
    assignments.append(tokens[start:set_end])
    for assignment in assignments:
        equals = next(
            (index for index, token in enumerate(assignment)
             if token.depth == block.depth and token.value == "="),
            None,
        )
        if equals is None:
            return False
        if any(
            _is_identifier(token) and token.value == required_column
            for token in assignment[:equals]
        ):
            return False
    target_open = next(
        (index for index in range(conflict_index + 2, do_index)
         if tokens[index].depth == block.depth and tokens[index].value == "("),
        None,
    )
    if target_open is None:
        # ON CONSTRAINT and unsupported conflict targets are intentionally rejected.
        return False
    target_close = next(
        (index for index in range(target_open + 1, do_index)
         if tokens[index].depth == block.depth and tokens[index].value == ")"),
        None,
    )
    if target_close is None:
        return False
    conflict_columns = {
        token.value for token in tokens[target_open + 1:target_close]
        if token.depth == block.depth + 1 and _is_identifier(token)
    }
    if required_column in conflict_columns:
        return True

    # A globally unique provider/message key may be intentionally preserved.
    # In that case, the UPDATE must be conditional on the existing row having
    # the same organization as EXCLUDED. Require the equality to be a complete
    # top-level conjunct; a textual match hidden under OR is not sufficient.
    where_index = next(
        (index for index in range(set_index + 1, block.end_index)
         if tokens[index].depth == block.depth and _is_keyword(tokens[index], "where")),
        None,
    )
    if where_index is None:
        return False
    where_end = next(
        (index for index in range(where_index + 1, block.end_index)
         if tokens[index].depth == block.depth and _is_keyword(tokens[index], "returning")),
        block.end_index,
    )
    condition = tokens[where_index + 1:where_end]
    condition, condition_depth = _strip_token_parentheses(condition, block.depth)
    if any(_is_keyword(token, "or") and token.depth == condition_depth for token in condition):
        return False
    start = 0
    conjuncts = []
    for index, token in enumerate(condition):
        if _is_keyword(token, "and") and token.depth == condition_depth:
            conjuncts.append(condition[start:index])
            start = index + 1
    conjuncts.append(condition[start:])
    expected_left = [
        target_alias, ".", "organization_id", "=", "excluded", ".", "organization_id",
    ]
    expected_right = [
        "excluded", ".", "organization_id", "=", target_alias, ".", "organization_id",
    ]
    for conjunct in conjuncts:
        conjunct, conjunct_depth = _strip_token_parentheses(conjunct, condition_depth)
        if any(_is_keyword(token, "or") and token.depth == conjunct_depth for token in conjunct):
            continue
        normalized = [token.value for token in conjunct]
        if normalized in (expected_left, expected_right):
            return True
    return False


def _update_mutates_organization_id(tokens: list[_SqlToken], block: _QueryBlock) -> bool:
    if block.kind != "update":
        return False
    set_index = next(
        (index for index in range(block.token_index + 1, block.end_index)
         if tokens[index].depth == block.depth and _is_keyword(tokens[index], "set")),
        None,
    )
    if set_index is None:
        return True
    set_end = next(
        (index for index in range(set_index + 1, block.end_index)
         if tokens[index].depth == block.depth
         and any(_is_keyword(tokens[index], value) for value in {"from", "where", "returning"})),
        block.end_index,
    )
    # Any organization_id reference in the assignment list is rejected
    # conservatively; this avoids confusing a RHS read with a safe target write.
    return any(
        _is_identifier(token) and token.value == "organization_id"
        for token in tokens[set_index + 1:set_end]
    )


def _parent_insert_has_scoped_source(
    sql: str,
    tokens: list[_SqlToken],
    insert_block: _QueryBlock,
    insert_refs: list[_TableReference],
    blocks: list[_QueryBlock],
) -> bool:
    del sql  # Kept in the signature to make this check's statement input explicit.
    for child in (ref for ref in insert_refs if ref.table in _PARENT_DERIVED_GUARDED_TABLES):
        parent_table, parent_column = _PARENT_RELATIONS[child.table]
        if not _upsert_conflict_target_is_scoped(
            tokens, insert_block, parent_column, child.alias,
        ):
            return False
        target = _insert_target_columns(tokens, insert_block, child)
        if target is None:
            return False
        columns, _close_index = target
        if parent_column not in columns:
            return False
        parent_position = columns.index(parent_column)
        source_block = next(
            (candidate for candidate in blocks
             if candidate.kind == "select"
             and candidate.depth == insert_block.depth
             and candidate.token_index > insert_block.token_index
             and candidate.token_index == insert_block.end_index),
            None,
        )
        if source_block is None:
            return False
        source_refs = _block_references(source_block, _table_references(tokens))
        parents = [ref for ref in source_refs if ref.table == parent_table]
        if len(parents) != 1:
            return False
        parent = parents[0]
        where = _where_tokens(tokens, source_block)
        if not _scope_params_for_alias(where, source_block.depth, parent.alias, False):
            return False
        projection_end = next(
            (index for index in range(source_block.token_index + 1, source_block.end_index)
             if tokens[index].depth == source_block.depth and _is_keyword(tokens[index], "from")),
            None,
        )
        if projection_end is None:
            return False
        projections = []
        start = source_block.token_index + 1
        for index in range(start, projection_end):
            if tokens[index].value == "," and tokens[index].depth == source_block.depth:
                projections.append(tokens[start:index])
                start = index + 1
        projections.append(tokens[start:projection_end])
        if parent_position >= len(projections):
            return False
        projection, projection_depth = _strip_token_parentheses(
            projections[parent_position], source_block.depth,
        )
        if not (
            len(projection) == 3
            and projection_depth == source_block.depth
            and projection[0].value == parent.alias
            and projection[1].value == "."
            and projection[2].value == "id"
        ):
            return False
    return True


def _has_parent_relation(
    tokens: list[_SqlToken], block: _QueryBlock, child: _TableReference,
    parent: _TableReference, parent_column: str,
) -> bool:
    expressions = [_where_tokens(tokens, block)]
    # An outer ON link can leave a preserved child without a matching scoped
    # parent. Require the positive link in WHERE for outer-join blocks.
    if not _outer_join_kinds(tokens, block):
        for ref in (child, parent):
            on_clause = _join_on_tokens(tokens, block, ref)
            if on_clause:
                expressions.append(on_clause)

    for expression in expressions:
        if not expression:
            continue
        expression, base_depth = _strip_token_parentheses(expression, block.depth)
        # A top-level OR means the relation can be bypassed. Parentheses around
        # the whole predicate are stripped above, so `(link OR TRUE)` also fails.
        if any(_is_keyword(token, "or") and token.depth == base_depth for token in expression):
            continue
        start = 0
        segments = []
        for index, token in enumerate(expression):
            if _is_keyword(token, "and") and token.depth == base_depth:
                segments.append(expression[start:index])
                start = index + 1
        segments.append(expression[start:])
        for segment in segments:
            segment, segment_depth = _strip_token_parentheses(segment, base_depth)
            if any(_is_keyword(token, "or") and token.depth == segment_depth for token in segment):
                continue
            values = [token.value for token in segment]
            positive_links = (
                [child.alias, ".", parent_column, "=", parent.alias, ".", "id"],
                [parent.alias, ".", "id", "=", child.alias, ".", parent_column],
            )
            if len(segment) == 7 and values in positive_links:
                return True
    return False


def _analyze_sql_scope(sql: str) -> tuple[set[str], tuple[int, ...]] | None:
    tokens = _tokenize_sql(sql)
    references = _table_references(tokens)
    touched = {ref.table for ref in references}
    scope_params = []
    blocks = _query_blocks(tokens)
    processed = set()
    for block in blocks:
        # This relation parser cannot safely inventory comma-separated FROM /
        # USING relations. Reject them even when the first relation is a
        # system/root table and a tenant table may be hidden after the comma.
        if _has_comma_join(tokens, block):
            return None
        refs = _block_references(block, references)
        direct = [ref for ref in refs if ref.table in TENANT_SCOPED_TABLES]
        parent_derived = [
            ref for ref in refs if ref.table in _PARENT_DERIVED_GUARDED_TABLES
        ]
        if not direct and not parent_derived:
            continue
        processed.update((ref.token_index, ref.operation) for ref in direct + parent_derived)
        if block.kind == "insert":
            if parent_derived and not _parent_insert_has_scoped_source(
                sql, tokens, block, refs, blocks,
            ):
                return None
            for ref in direct:
                if not _upsert_conflict_target_is_scoped(
                    tokens, block, "organization_id", ref.alias,
                ):
                    return None
                params = _insert_scope_params(tokens, block, ref)
                if not params:
                    return None
                scope_params.extend(params)
            continue
        if block.kind not in {"select", "update", "delete"}:
            return None
        if direct and _update_mutates_organization_id(tokens, block):
            return None
        where = _where_tokens(tokens, block)
        for ref in direct:
            params = _scope_params_for_reference(
                tokens, block, ref, where, allow_unqualified=len(direct) == 1,
            )
            if not params:
                return None
            scope_params.extend(params)
        for attempt in parent_derived:
            if block.kind != "select":
                return None
            parent_table, parent_column = _PARENT_RELATIONS[attempt.table]
            parents = [ref for ref in direct if ref.table == parent_table]
            linked_and_scoped = False
            for parent in parents:
                if _has_parent_relation(
                    tokens, block, attempt, parent, parent_column,
                ):
                    if _scope_params_for_reference(
                        tokens, block, parent, where, allow_unqualified=False,
                    ):
                        linked_and_scoped = True
                        break
            if not linked_and_scoped:
                return None
    guarded_refs = [
        ref for ref in references
        if ref.table in TENANT_SCOPED_TABLES or ref.table in _PARENT_DERIVED_GUARDED_TABLES
    ]
    if any((ref.token_index, ref.operation) not in processed for ref in guarded_refs):
        return None
    return touched, tuple(sorted(set(scope_params)))


class MissingOrganizationIdError(TypeError):
    """organization_id e' obbligatorio: chiamato con None o senza argomento."""


class TenantScopeViolation(RuntimeError):
    """Statement su tabella tenant-scoped senza filtro organization_id."""


def extract_tables(sql: str) -> set[str]:
    """Return relation names, ignoring comments/strings but retaining quoted names."""
    tokens = _tokenize_sql(sql)
    if any(_has_comma_join(tokens, block) for block in _query_blocks(tokens)):
        raise ValueError("comma-separated FROM/USING relations are unsupported")
    return {ref.table for ref in _table_references(tokens)}


def has_explicit_org_scope(sql: str, tables: set[str]) -> bool:
    """Check every reference in its own SQL query block.

    The conservative tokenizer rejects unsupported syntax. A passing shape is
    not proof the bind value equals an authenticated org; runtime callers also
    compare all recognized tenant binds with the ScopedConnection org.
    """
    analysis = _analyze_sql_scope(sql)
    return analysis is not None and tables <= analysis[0]


def assert_org_scoped(sql: str, organization_id=None, args=()) -> None:
    """Fail-closed: rifiuta statement su tabelle tenant-scoped che non
    hanno un predicato organization_id verificabile sintatticamente.

    ScopedConnection passa anche il proprio organization_id e gli argomenti:
    ogni bind usato dallo scope deve essere presente e UUID-equivalente.
    """
    tokens = _tokenize_sql(sql)
    if not all(
        tokens[index].kind == "IDENT"
        and tokens[index].value in _SUPPORTED_STATEMENT_STARTS
        for index in _statement_start_indexes(tokens)
    ):
        raise TenantScopeViolation("ScopedConnection rifiuta DDL/COPY/LOCK e SQL non supportato")
    if any(_has_comma_join(tokens, block) for block in _query_blocks(tokens)):
        raise TenantScopeViolation(
            "ScopedConnection rifiuta relazioni FROM/USING separate da virgola"
        )
    touched = {ref.table for ref in _table_references(tokens)}
    guarded = touched & (TENANT_SCOPED_TABLES | _PARENT_DERIVED_GUARDED_TABLES)
    analysis = _analyze_sql_scope(sql)
    if guarded and analysis is None:
        raise TenantScopeViolation(
            f"Query su tabelle tenant-scoped/parent-derived {sorted(guarded)} "
            f"senza scope verificabile per query-block: {sql[:120]!r}"
        )
    if guarded and organization_id is not None:
        expected = uuid.UUID(str(organization_id))
        for position in analysis[1]:
            if position < 1 or position > len(args):
                raise TenantScopeViolation(
                    f"Bind tenant ${position} assente dalla query: {sql[:120]!r}"
                )
            try:
                actual = uuid.UUID(str(args[position - 1]))
            except (TypeError, ValueError, AttributeError) as exc:
                raise TenantScopeViolation(
                    f"Bind tenant ${position} non e' un UUID valido"
                ) from exc
            if actual != expected:
                raise TenantScopeViolation(
                    f"Bind tenant ${position} non corrisponde alla connessione scoped"
                )


class ScopedConnection:
    """Proxy fail-closed su una connessione asyncpg gia' vincolata a un org.

    Espone solo fetch/fetchrow/fetchval/execute/executemany (ognuno passa
    da assert_org_scoped PRIMA di delegare) e transaction(). Qualsiasi
    altro attributo e' rifiutato: niente scorciatoie fuori dal guard.
    """

    __slots__ = ("_conn", "_org")

    def __init__(self, conn, organization_id: uuid.UUID):
        self._conn = conn
        self._org = organization_id

    @property
    def organization_id(self) -> uuid.UUID:
        return self._org

    async def fetch(self, sql, *args, **kwargs):
        assert_org_scoped(sql, self._org, args)
        return await self._conn.fetch(sql, *args, **kwargs)

    async def fetchrow(self, sql, *args, **kwargs):
        assert_org_scoped(sql, self._org, args)
        return await self._conn.fetchrow(sql, *args, **kwargs)

    async def fetchval(self, sql, *args, **kwargs):
        assert_org_scoped(sql, self._org, args)
        return await self._conn.fetchval(sql, *args, **kwargs)

    async def execute(self, sql, *args, **kwargs):
        assert_org_scoped(sql, self._org, args)
        return await self._conn.execute(sql, *args, **kwargs)

    async def executemany(self, sql, args_seq, *args, **kwargs):
        assert_org_scoped(sql)
        rows = list(args_seq)
        for row in rows:
            assert_org_scoped(sql, self._org, row)
        return await self._conn.executemany(sql, rows, *args, **kwargs)

    def transaction(self):
        return self._conn.transaction()

    def __getattr__(self, name):
        raise AttributeError(
            f"ScopedConnection non espone {name!r}: usa fetch/fetchrow/"
            f"fetchval/execute/executemany/transaction (query senza passare "
            f"dal guard tenant-scope sono vietate)"
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostica
        return f"<ScopedConnection org={self._org}>"


class TenantScopedRepository:
    """Mixin per le classi repository: aggiunge scoped_conn(organization_id).

    Uso: `async with self.scoped_conn(org_id) as conn:` al posto di
    `async with self.pool.acquire() as conn:` — ogni statement delegato
    viene validato contro il filtro organization_id.
    """

    @asynccontextmanager
    async def scoped_conn(self, organization_id):
        if organization_id is None:
            raise MissingOrganizationIdError(
                "organization_id e' obbligatorio: nessuna connessione "
                "tenant-scoped senza org (fail-closed)"
            )
        org = uuid.UUID(str(organization_id))
        async with self.pool.acquire() as conn:
            yield ScopedConnection(conn, org)


def system_scope(reason: str):
    """Decorator per le eccezioni intenzionali cross-tenant (tenant
    resolution da webhook, worker globali, retention). Il motivo e'
    obbligatorio e finisce nell'attributo __system_scope__ letto dal
    check CI."""
    if not reason or not isinstance(reason, str):
        raise TypeError("system_scope richiede un motivo (str) obbligatorio")

    def decorator(fn):
        fn.__system_scope__ = reason
        return fn

    return decorator
