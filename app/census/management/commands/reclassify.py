from django.core.management.base import BaseCommand

from census import classify
from census.models import Dataset, Topic


class Command(BaseCommand):
    help = (
        "Re-apply topic rules and name-field suggestions to every "
        "dataset (run after editing topic rules in the admin). Manual choices are kept."
    )

    def handle(self, *args, **options):
        topics = list(Topic.objects.all())
        n = 0
        for dataset in Dataset.objects.select_related("service").iterator():
            classify.apply_topics(dataset, topics)
            updates = ["search_text"]
            dataset.search_text = classify.search_text(dataset)
            if not dataset.name_fields_manual:
                dataset.name_fields = classify.suggest_name_fields(dataset.fields)
                updates.append("name_fields")
            dataset.save(update_fields=updates)
            n += 1
        self.stdout.write(self.style.SUCCESS(f"Reclassified {n} datasets"))
