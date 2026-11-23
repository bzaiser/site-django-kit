from django.db import models
from django.utils.text import slugify


class SiteConfig(models.Model):
    site_name = models.CharField(max_length=140, default="Mein Verein")
    tagline = models.CharField(max_length=220, blank=True, default="")
    logo_url = models.URLField(blank=True, default="")
    footer_text = models.CharField(max_length=220, blank=True, default="")
    imprint_text = models.TextField(blank=True, default="")
    privacy_text = models.TextField(blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    default_genre = models.CharField(max_length=40, blank=True, default="")
    member_login_url = models.CharField(max_length=160, default="/accounts/login/")
    member_app_url = models.CharField(max_length=160, default="/app/")

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.site_name


class PublicPage(models.Model):
    title = models.CharField(max_length=160)
    slug = models.SlugField(unique=True, max_length=180)
    is_home = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    meta_description = models.CharField(max_length=255, blank=True, default="")
    genre = models.CharField(max_length=40, blank=True, default="")
    menu_order = models.PositiveIntegerField(default=10)
    show_in_nav = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["menu_order", "title"]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:160] or "seite"
            cand = base
            idx = 2
            while PublicPage.objects.filter(slug=cand).exclude(pk=self.pk).exists():
                cand = f"{base[:150]}-{idx}"
                idx += 1
            self.slug = cand
        if self.is_home:
            PublicPage.objects.filter(is_home=True).exclude(pk=self.pk).update(is_home=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class PageBlock(models.Model):
    BLOCK_TYPE_CHOICES = [
        ("hero", "Hero-Kopfbereich"),
        ("text_image", "Text & Bild"),
        ("news_feed", "News-Feed (list_kit)"),
        ("upcoming_events", "Nächste Termine (cal_kit)"),
        ("people_grid", "Vorstand / Kontakte (domain_kit)"),
        ("downloads", "Downloads (domain_kit)"),
        ("faq_list", "FAQ-Akkordeon (list_kit)"),
        ("public_form", "Öffentliches Formular (list_kit)"),
        ("stats_bar", "Kennzahlen-Leiste"),
    ]

    page = models.ForeignKey(PublicPage, on_delete=models.CASCADE, related_name="blocks")
    block_type = models.CharField(max_length=32, choices=BLOCK_TYPE_CHOICES, default="text_image")
    title = models.CharField(max_length=180, blank=True, default="")
    subtitle = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    image_url = models.URLField(blank=True, default="")
    cta_label = models.CharField(max_length=80, blank=True, default="")
    cta_url = models.CharField(max_length=255, blank=True, default="")
    config_json = models.JSONField(default=dict, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.page.title} · {self.get_block_type_display()}"
