from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(
        name="CutoutRecord",
        fields=[
            ("idempotency_key", models.CharField(max_length=64, primary_key=True, serialize=False)),
            ("product_id", models.CharField(max_length=64)),
            ("artifact_uri", models.TextField()),
            ("source_url", models.URLField(max_length=4096)),
            ("object_key", models.CharField(max_length=512)),
            ("ra_deg", models.FloatField()),
            ("dec_deg", models.FloatField()),
            ("size_deg", models.FloatField()),
            ("detector", models.CharField(max_length=16)),
            ("time_bounds_lower", models.FloatField()),
            ("fits_version", models.CharField(max_length=100, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ], options={"db_table": "cutouts"},
    )]
