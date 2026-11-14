from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("observation", "0003_observation_metadata_cache")]
    operations = [migrations.CreateModel(
        name="CatalogGeneration",
        fields=[
            ("id", models.PositiveSmallIntegerField(primary_key=True, default=1, serialize=False)),
            ("version", models.PositiveBigIntegerField(default=0)),
        ], options={"db_table": "catalog_generation"},
    )]
