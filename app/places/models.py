from django.contrib.gis.db import models
from django.urls import reverse


class JurisdictionQuerySet(models.QuerySet):
    def current(self):
        """Places in force today: not Shannon County, not a merged school district."""
        return self.filter(valid_to=None)

    def governments(self):
        """Kinds that govern and publish: the state, counties, cities and towns, tribes."""
        return self.filter(kind__in=["state", "county", "place", "tribal"])


class Jurisdiction(models.Model):
    """A place records are about, with its Census boundary. Rows come from sdplaces (names,
    GEOIDs, parents, OCD IDs, dates in force) and shapes from Census cartographic boundary
    files. Former places stay, with valid_to and a successor (Shannon County → Oglala Lakota
    County), so records that name them still land somewhere."""

    class Kind(models.TextChoices):
        STATE = "state"
        COUNTY = "county"
        PLACE = "place", "City or town"
        CDP = "cdp", "Community (census designated place)"
        TRIBAL = "tribal", "Tribal area"
        SCHOOL_DISTRICT = "school_district", "School district"
        SLDU = "sldu", "State Senate district"
        SLDL = "sldl", "State House district"

    kind = models.CharField(max_length=20, choices=Kind.choices, db_index=True)
    geoid = models.CharField(max_length=20, help_text="Census GEOID")
    geoidfq = models.CharField(
        max_length=40,
        unique=True,
        help_text="Fully qualified Census GEOID (0500000US46081), unique across kinds",
    )
    ocd_id = models.CharField(max_length=255, blank=True, help_text="Open Civic Data division ID")
    name = models.CharField(max_length=255, help_text='Full name, e.g. "Lawrence County"')
    short_name = models.CharField(max_length=255, help_text='e.g. "Lawrence"')
    slug = models.SlugField(max_length=255, unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True, help_text="Set for former places")
    successor = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="predecessors",
        help_text="The place it became (Shannon County → Oglala Lakota County)",
    )
    boundary = models.MultiPolygonField(srid=4326, null=True, blank=True)

    objects = JurisdictionQuerySet.as_manager()

    class Meta:
        ordering = ["kind", "name"]
        constraints = [
            models.UniqueConstraint(fields=["kind", "geoid"], name="unique_jurisdiction_geoid")
        ]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("census:place_detail", args=[self.slug])
