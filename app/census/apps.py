from django.apps import AppConfig


class CensusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "census"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from census import seed
        from census.models import Dataset, Publisher, Source

        # Keep seeds/sources.toml in step with the database
        for model in (Publisher, Source, Dataset):
            post_save.connect(seed.on_change, sender=model, dispatch_uid=f"seed-{model.__name__}")
            post_delete.connect(
                seed.on_change, sender=model, dispatch_uid=f"seed-del-{model.__name__}"
            )
