from django.db import models


class ObservationRecord(models.Model):
    publisher_did = models.CharField(max_length=512, primary_key=True)
    collection = models.CharField(max_length=100)
    access_url = models.URLField(max_length=2048)
    ra_deg = models.FloatField(null=True)
    dec_deg = models.FloatField(null=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "observations"
