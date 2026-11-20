# syntax=docker/dockerfile:1

# Frontend statico (restyle): nginx serve landing su "/" e dashboard su "/app/"
# in un unico container. In produzione il backend Ã¨ raggiunto same-origin
# su "/api/*" (proxy Traefik): MELPIS_API_BASE resta vuota -> niente CORS,
# cookie same-site.
#
# Build context: web/   -> docker build -f web/Dockerfile web/
# Per l'envsubst serve gettext; l'entrypoint ufficiale nginx esegue gli
# script in /docker-entrypoint.d/ prima di avviare il daemon.

FROM node:24-alpine AS dashboard-builder

WORKDIR /workspace
COPY dashboard-src/ ./web/dashboard-src/
COPY scripts/compose-dashboard.mjs ./web/scripts/compose-dashboard.mjs
COPY dashboard-css/ ./web/dashboard-css/
COPY scripts/compose-dashboard-css.mjs ./web/scripts/compose-dashboard-css.mjs
COPY index.html ./web/index.html
COPY style.css ./web/style.css
RUN node ./web/scripts/compose-dashboard.mjs --output /tmp/dashboard.html && \
    cmp --silent /tmp/dashboard.html ./web/index.html && \
    node ./web/scripts/compose-dashboard-css.mjs --output /tmp/style.css && \
    cmp --silent /tmp/style.css ./web/style.css

FROM nginx:1.31-alpine AS runtime

# â”€â”€ Landing e pagine pubbliche multilingua (root del docroot) â”€
COPY landing/index.html landing/style.css landing/app.js landing/navbar.js /usr/share/nginx/html/
COPY design-tokens.css /usr/share/nginx/html/
COPY landing/404.html /usr/share/nginx/html/
COPY landing/robots.txt landing/sitemap.xml landing/*.jpg /usr/share/nginx/html/
COPY landing/*.webp landing/*.svg /usr/share/nginx/html/
COPY landing/brand/ /usr/share/nginx/html/brand/
COPY inline-assets/ /usr/share/nginx/html/inline-assets/
COPY landing/fonts/ /usr/share/nginx/html/fonts/
COPY landing/settori/ /usr/share/nginx/html/settori/
COPY landing/prezzi/ /usr/share/nginx/html/prezzi/
COPY landing/documentazione/ /usr/share/nginx/html/documentazione/
COPY landing/en/ /usr/share/nginx/html/en/
COPY landing/es/ /usr/share/nginx/html/es/
COPY landing/fr/ /usr/share/nginx/html/fr/
COPY landing/de/ /usr/share/nginx/html/de/
COPY locales/ /usr/share/nginx/html/locales/
COPY i18n-client.js /usr/share/nginx/html/

# â”€â”€ Pagine di accesso e registrazione dedicate (/accedi/, /registrati/) â”€â”€
COPY login.html login.js register.html register.js auth.js auth.css /usr/share/nginx/html/

# â”€â”€ Dashboard (sotto /app/) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
COPY --from=dashboard-builder /tmp/style.css /usr/share/nginx/html/app/style.css
COPY app.js dashboard-auth-transport.js dashboard-ai-settings.js dashboard-settings.js dashboard-integrations.js dashboard-router.js dashboard-shared.js dashboard-inbox.js dashboard-overview.js dashboard-reviews.js dashboard-knowledge.js dashboard-team.js dashboard-ai-simulator.js dashboard-bookings.js dialog-focus.js theme-init.js mfa.js i18n-client.js /usr/share/nginx/html/app/
COPY dashboard-booking-catalog-form.js /usr/share/nginx/html/app/
COPY dashboard-booking-catalog.js /usr/share/nginx/html/app/
COPY dashboard-booking-catalog.css /usr/share/nginx/html/app/
COPY --from=dashboard-builder /tmp/dashboard.html /usr/share/nginx/html/app/index.html
COPY design-tokens.css /usr/share/nginx/html/app/
COPY fonts/ /usr/share/nginx/html/app/fonts/
COPY vendor/ /usr/share/nginx/html/app/vendor/
COPY config.template.js /usr/share/nginx/html/config.template.js

# Public legal pages are templates until the entrypoint renders only the
# reviewed LEGAL_* fields into /run/melpis-legal (a tmpfs in production).
# Keep templates outside the docroot so unrendered tokens can never ship.
COPY landing/privacy.html landing/termini.html landing/cookie.html /opt/melpis/legal-templates/
COPY landing/en/ /opt/melpis/legal-templates/en/
COPY landing/es/ /opt/melpis/legal-templates/es/
COPY landing/fr/ /opt/melpis/legal-templates/fr/
COPY landing/de/ /opt/melpis/legal-templates/de/
COPY render_legal_pages.py /opt/melpis/render_legal_pages.py
COPY docker-entrypoint.d/50-render-legal-pages.sh /docker-entrypoint.d/50-render-legal-pages.sh

# Genera config.js dal template con envsubst nella root pubblica.
# MELPIS_API_BASE vuota (default) = same-origin.
RUN apk add --no-cache gettext python3 && \
    envsubst '${MELPIS_API_BASE}' < /usr/share/nginx/html/config.template.js \
        > /usr/share/nginx/html/config.js && \
    rm /usr/share/nginx/html/config.template.js && \
    rm -f /usr/share/nginx/html/app/vendor/fullcalendar-source.min.js && \
    rm -f /usr/share/nginx/html/privacy.html /usr/share/nginx/html/termini.html /usr/share/nginx/html/cookie.html \
        /usr/share/nginx/html/en/privacy/index.html /usr/share/nginx/html/en/terms/index.html /usr/share/nginx/html/en/cookies/index.html \
        /usr/share/nginx/html/es/privacidad/index.html /usr/share/nginx/html/es/terminos/index.html /usr/share/nginx/html/es/cookies/index.html \
        /usr/share/nginx/html/fr/confidentialite/index.html /usr/share/nginx/html/fr/conditions/index.html /usr/share/nginx/html/fr/cookies/index.html \
        /usr/share/nginx/html/de/datenschutz/index.html /usr/share/nginx/html/de/agb/index.html /usr/share/nginx/html/de/cookies/index.html && \
    sed -i 's/\r$//' /docker-entrypoint.d/50-render-legal-pages.sh && \
    chmod 0755 /docker-entrypoint.d/50-render-legal-pages.sh && \
    chown -R nginx:nginx /usr/share/nginx/html

# Sostituisce il default.conf dell'immagine base.
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/conf.d/security-headers.conf

EXPOSE 80
