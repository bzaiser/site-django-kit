from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PublicPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=160)),
                ("slug", models.SlugField(max_length=180, unique=True)),
                ("is_home", models.BooleanField(default=False)),
                ("is_published", models.BooleanField(default=True)),
                ("meta_description", models.CharField(blank=True, default="", max_length=255)),
                ("genre", models.CharField(blank=True, default="", max_length=40)),
                ("menu_order", models.PositiveIntegerField(default=10)),
                ("show_in_nav", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["menu_order", "title"]},
        ),
        migrations.CreateModel(
            name="SiteConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("site_name", models.CharField(default="Mein Verein", max_length=140)),
                ("tagline", models.CharField(blank=True, default="", max_length=220)),
                ("logo_url", models.URLField(blank=True, default="")),
                ("footer_text", models.CharField(blank=True, default="", max_length=220)),
                ("imprint_text", models.TextField(blank=True, default="")),
                ("privacy_text", models.TextField(blank=True, default="")),
                ("contact_email", models.EmailField(blank=True, default="", max_length=254)),
                ("default_genre", models.CharField(blank=True, default="", max_length=40)),
                ("member_login_url", models.CharField(default="/accounts/login/", max_length=160)),
                ("member_app_url", models.CharField(default="/app/", max_length=160)),
            ],
        ),
        migrations.CreateModel(
            name="PageBlock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("block_type", models.CharField(choices=[("hero", "Hero-Kopfbereich"), ("text_image", "Text & Bild"), ("news_feed", "News-Feed (list_kit)"), ("upcoming_events", "Nächste Termine (cal_kit)"), ("people_grid", "Vorstand / Kontakte (domain_kit)"), ("downloads", "Downloads (domain_kit)"), ("faq_list", "FAQ-Akkordeon (list_kit)"), ("public_form", "Öffentliches Formular (list_kit)"), ("stats_bar", "Kennzahlen-Leiste")], default="text_image", max_length=32)),
                ("title", models.CharField(blank=True, default="", max_length=180)),
                ("subtitle", models.CharField(blank=True, default="", max_length=255)),
                ("body", models.TextField(blank=True, default="")),
                ("image_url", models.URLField(blank=True, default="")),
                ("cta_label", models.CharField(blank=True, default="", max_length=80)),
                ("cta_url", models.CharField(blank=True, default="", max_length=255)),
                ("config_json", models.JSONField(blank=True, default=dict)),
                ("position", models.PositiveIntegerField(default=0)),
                ("is_visible", models.BooleanField(default=True)),
                ("page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="blocks", to="site_kit.publicpage")),
            ],
            options={"ordering": ["position", "id"]},
        ),
    ]
