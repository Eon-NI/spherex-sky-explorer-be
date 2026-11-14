from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("observation", "0002_temporal_observation")]
    operations = [
        migrations.AddField("observationrecord", "obs_id", models.CharField(max_length=256, null=True)),
        migrations.AddField("observationrecord", "access_format", models.CharField(max_length=100, null=True)),
        migrations.AddField("observationrecord", "s_region", models.TextField(null=True)),
        migrations.AddField("observationrecord", "energy_bandpassname", models.CharField(max_length=100, null=True)),
        migrations.AddField("observationrecord", "t_min_mjd", models.FloatField(null=True)),
        migrations.AddField("observationrecord", "t_max_mjd", models.FloatField(null=True)),
        migrations.AddField("observationrecord", "em_min_m", models.FloatField(null=True)),
        migrations.AddField("observationrecord", "em_max_m", models.FloatField(null=True)),
        migrations.AddField("observationrecord", "processing_version", models.CharField(max_length=100, null=True)),
        migrations.AddField("observationrecord", "missing_fields", models.JSONField(default=list)),
        migrations.CreateModel(
            name="ObservationSearchSnapshot",
            fields=[
                ("query_key", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("observation_ids", models.JSONField(default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ], options={"db_table": "observation_search_snapshots"},
        ),
    ]
