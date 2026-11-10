from django.contrib.postgres.indexes import GinIndex
from django.db import models


class Mention(models.Model):
    """One name as a record gives it: one party of a parcel's owner string, one filer, one
    legislator's session profile. Typed with Follow the Money schemas. Matching groups
    mentions into entities later; nothing here decides two mentions are the same."""

    class Schema(models.TextChoices):
        PERSON = "Person"
        ORGANIZATION = "Organization"
        COMPANY = "Company"
        PUBLIC_BODY = "PublicBody", "Public body"
        LEGAL_ENTITY = "LegalEntity", "Legal entity (trust, estate)"

    schema = models.CharField(max_length=20, choices=Schema.choices, db_index=True)
    name = models.CharField(max_length=500, help_text="This party's name, as written")
    normalized = models.CharField(max_length=500, db_index=True, help_text="sdnames.normalize")
    properties = models.JSONField(
        default=dict, blank=True, help_text="FtM properties, as lists: firstName, address, ..."
    )

    # Where it came from
    dataset = models.CharField(max_length=100, db_index=True, help_text="catalog/datasets.toml id")
    record_key = models.CharField(max_length=255, help_text="The source's own key for the record")
    field = models.CharField(max_length=100, blank=True, help_text="Where the name was (OWNER_NAM)")
    position = models.PositiveSmallIntegerField(
        default=0, help_text="Which party in the string, from 0"
    )
    role = models.CharField(
        max_length=50,
        blank=True,
        help_text="trustee, care of, spouse; or the source's (legislator)",
    )
    original = models.TextField(blank=True, help_text="The whole string as written")
    url = models.URLField(max_length=1000, blank=True, help_text="The primary source")
    context = models.JSONField(
        default=dict, blank=True, help_text="Source fields that help matching (district, year)"
    )
    jurisdiction = models.ForeignKey(
        "places.Jurisdiction", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="mentions",
    )  # fmt: skip
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)

    # Matching
    entity_id = models.CharField(
        max_length=100, blank=True, db_index=True, help_text="Assigned by matching"
    )
    gold_entity_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        help_text="Known by hand: mentions that share one are the same entity",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "record_key", "field", "position"], name="unique_mention"
            )
        ]
        indexes = [
            GinIndex(fields=["normalized"], name="mention_name_trgm", opclasses=["gin_trgm_ops"])
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.dataset} {self.record_key})"
