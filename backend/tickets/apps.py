import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class TicketsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tickets"

    def ready(self) -> None:
        """Called once when Django finishes loading all apps.

        We load the FAQ embeddings here so they are in memory for the
        lifetime of the process without being re-loaded on every request.

        Guards:
        - The `RUN_MAIN` environment variable is set by Django's auto-reloader
          only in the *child* reloader process, so we skip the load in the
          parent watcher process to avoid a double-load in dev mode.
        - We catch all exceptions so a missing DB table (e.g. before the first
          migration is applied) never prevents the server from starting.
        """
        import os

        # In development, Django's auto-reloader forks twice. Skip the first
        # fork (the file watcher) to avoid loading the model before the DB is
        # ready. In production (gunicorn), RUN_MAIN is not set, so we always load.
        if os.environ.get("RUN_MAIN") == "true" or not os.environ.get("RUN_MAIN"):
            self._load_faq_embeddings()

    @staticmethod
    def _load_faq_embeddings() -> None:
        try:
            from tickets.services.faq_service import load_faq_embeddings  # noqa

            load_faq_embeddings()
        except Exception as exc:  # noqa: BLE001
            # Don't crash startup — the table may not exist yet.
            logger.warning("Could not load FAQ embeddings at startup: %s", exc)
