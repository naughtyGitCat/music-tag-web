# Generated by Django 2.2.6 on 2022-01-28 07:56

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flow', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='node',
            name='uuid',
            field=models.CharField(max_length=255, unique=True, verbose_name='UUID'),
        ),
    ]
