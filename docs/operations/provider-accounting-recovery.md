# Recupero contabilizzazione provider AI (A03)

## Procedura per titolare o manager

1. Aprire **Impostazioni → Piano e abbonamento**. La sezione **Verifica contabilizzazione AI** compare solo quando il tenant ha tentativi non risolti.
2. Attendere che il lease del worker scada. Un tentativo ancora attivo mostra **Aggiorna stato**; il controllo non invia una nuova generazione.
3. Quando disponibile, premere **Verifica esito e ripristina** e confermare l'accesso con MFA recente (AAL2, ultimi 5 minuti).
4. Il backend risolve un tentativo come `not_sent` solo se la richiesta non aveva mai ricevuto l'autorizzazione persistente di dispatch. Per un dispatch già autorizzato, recupera soltanto metriche verificate o evidenza provider attendibile. Non accetta token o costi inseriti dall'utente.

La procedura è utilizzabile da un solo titolare o manager attivo: non richiede la presenza di un secondo manager del cliente. Le transizioni di fencing e recovery aggiungono evidenza append-only tenant-scoped; le riconciliazioni registrano anche l'audit nella stessa transazione.

## Stati e risultati

| Situazione | Risultato |
| --- | --- |
| Errore locale prima del dispatch | `not_sent`; metriche e costo restano nulli, il blocco è ricalcolato. |
| Evidenza provider attendibile che conferma assenza di addebito dopo un dispatch | `resolved`; resta distinto da `not_sent`, perché l'HTTP potrebbe essere stato inviato. |
| Dispatch autorizzato ma risposta incerta | Il tenant resta bloccato. Un timeout o un lease scaduto non provano consumo zero. |
| OpenRouter ha fornito un generation ID | Il backend usa solo la GET autenticata dei metadati della generazione, verifica tenant/correlazione, modello, orario e token nativi, quindi registra il costo provider USD e la stima applicativa EUR quando disponibile. L'output non viene riprodotto. |
| Risposta tardiva di un worker già fenced | Viene conservata come evidenza legata alla sua epoca; un worker autorizzato può riconciliarla una sola volta. Il worker tardivo non può finalizzare il ledger. |
| Nessuna evidenza, mismatch o prezzo EUR non disponibile | Il tentativo rimane bloccato e visibile. Non viene ritentata la generazione né inventato un costo. |

Groq non dispone qui di una riconciliazione automatica tramite generation metadata: i suoi tentativi incerti rimangono bloccati finché non esistono prove attendibili. La procedura preesistente basata su proposta e approvazione con evidenze rimane disponibile per la revisione manuale.

## Confine di sicurezza e rollout

Non viene introdotto uno store WORM esterno o un servizio a pagamento. `provider_attempt_evidence` è tenant-scoped, non esposto ai ruoli client e, per `service_role`, consente lettura/inserimento senza update o delete. I record di audit sono scritti nella stessa transazione del cambiamento. Alla cancellazione dell'organizzazione, runtime ed evidenze seguono la retention già prevista per `usage_events`: perdono lo scope tenant, ma restano collegati all'evento storico. Se l'evento viene rimosso dalla sua policy di retention, le righe A03 collegate vengono rimosse con esso. Questa garanzia vale entro il confine amministrativo del database; un amministratore privilegiato del database resta in grado di alterare lo storage e non viene presentato come fonte indipendente di verità.

La migrazione `061_provider_attempt_fencing.sql` è additiva e va applicata in Staging attraverso il processo approvato prima di rendere disponibili route e dashboard. Nessuna migrazione remota è eseguita da questa PR. Non si devono rilasciare route senza lo schema corrispondente.
