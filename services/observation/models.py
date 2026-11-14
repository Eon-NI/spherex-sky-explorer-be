from django.db import models


class ObservationRecord(models.Model):
    publisher_did = models.CharField(max_length=512, primary_key=True)
    collection = models.CharField(max_length=100)
    access_url = models.URLField(max_length=2048)
    ra_deg = models.FloatField(null=True)
    dec_deg = models.FloatField(null=True)
    obs_id = models.CharField(max_length=256, null=True)
    access_format = models.CharField(max_length=100, null=True)
    s_region = models.TextField(null=True)
    energy_bandpassname = models.CharField(max_length=100, null=True)
    t_min_mjd = models.FloatField(null=True)
    t_max_mjd = models.FloatField(null=True)
    em_min_m = models.FloatField(null=True)
    em_max_m = models.FloatField(null=True)
    processing_version = models.CharField(max_length=100, null=True)
    missing_fields = models.JSONField(default=list)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "observations"


class TemporalObservationRecord(models.Model):
    product_id = models.CharField(max_length=64, primary_key=True)
    artifact_uri = models.TextField()
    cutout_url = models.URLField(max_length=4096)
    time_bounds_lower = models.FloatField()
    ra_deg = models.FloatField()
    dec_deg = models.FloatField()
    size_deg = models.FloatField()
    detector = models.CharField(max_length=16)
    discovered_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "temporal_observations"


class ObservationSearchSnapshot(models.Model):
    query_key = models.CharField(max_length=64, primary_key=True)
    observation_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "observation_search_snapshots"


class CatalogGeneration(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    version = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "catalog_generation"
