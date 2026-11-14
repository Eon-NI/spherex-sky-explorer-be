from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("observation", "0001_initial")]
    operations = [migrations.CreateModel(
        name="TemporalObservationRecord",
        fields=[
            ("product_id", models.CharField(max_length=64, primary_key=True, serialize=False)),
            ("artifact_uri", models.TextField()),
            ("cutout_url", models.URLField(max_length=4096)),
            ("time_bounds_lower", models.FloatField()),
            ("ra_deg", models.FloatField()),
            ("dec_deg", models.FloatField()),
            ("size_deg", models.FloatField()),
            ("detector", models.CharField(max_length=16)),
            ("discovered_at", models.DateTimeField(auto_now=True)),
        ], options={"db_table": "temporal_observations"},
    )]
