from django.db import models
from django.utils.text import slugify


class SiteConfig(models.Model):
    CONTAINER_WIDTH_CHOICES = [
        ("narrow", "Schmal (max. 960 px)"),
        ("standard", "Mittel (max. 1140 px)"),
        ("wide", "Breit (max. 1380 px)"),
        ("full", "Fensterbreite (100 %)"),
    ]
    PAGE_MARGIN_CHOICES = [
        ("minimal", "Minimal (Smartphone-nah)"),
        ("compact", "Schmal"),
        ("normal", "Mittel"),
        ("wide", "Großzügig"),
    ]

    site_name = models.CharField(max_length=140, default="Mein Verein")
    tagline = models.CharField(max_length=220, blank=True, default="")
    logo = models.FileField(upload_to="site_kit/logo/", blank=True, null=True)
    logo_url = models.URLField(blank=True, default="")
    container_width = models.CharField(max_length=16, choices=CONTAINER_WIDTH_CHOICES, default="standard")
    page_margin = models.CharField(max_length=16, choices=PAGE_MARGIN_CHOICES, default="normal")
    footer_text = models.CharField(max_length=220, blank=True, default="")
    imprint_text = models.TextField(blank=True, default="")
    privacy_text = models.TextField(blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    default_genre = models.CharField(max_length=40, blank=True, default="")
    member_login_url = models.CharField(max_length=160, default="/accounts/login/")
    member_app_url = models.CharField(max_length=160, default="/app/")
    events_layout = models.CharField(max_length=16, default="row")
    events_image_position = models.CharField(max_length=16, default="left")

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return self.site_name

    @property
    def resolved_logo_url(self) -> str:
        if self.logo:
            try:
                return self.logo.url
            except Exception:
                pass
        return (self.logo_url or "").strip()

    @property
    def container_class(self) -> str:
        cw = self.container_width or "standard"
        pm = self.page_margin or "normal"
        if cw == "full":
            base = "container-fluid"
        elif cw == "wide":
            base = "container-xxl"
        else:
            base = "container"
        return f"{base} site-container site-w-{cw} site-m-{pm}"


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
        ("annual_program", "Jahresprogramm / Angebotskatalog (cal_kit)"),
        ("photo_gallery", "Bildergalerie / Impressionen (list_kit)"),
        ("video", "Video (Upload / YouTube / Vimeo)"),
        ("stats_bar", "Kennzahlen-Leiste"),
        ("rich_html", "HTML / WYSIWYG (Admin-Layout)"),
    ]
    COL_SPAN_CHOICES = [
        (12, "Volle Breite (12/12)"),
        (8, "Zwei Drittel (8/12)"),
        (6, "Halbe Breite (6/12)"),
        (4, "Drittel Breite (4/12)"),
        (3, "Viertel Breite (3/12)"),
    ]

    page = models.ForeignKey(PublicPage, on_delete=models.CASCADE, related_name="blocks")
    block_type = models.CharField(max_length=32, choices=BLOCK_TYPE_CHOICES, default="text_image")
    col_span = models.PositiveSmallIntegerField(choices=COL_SPAN_CHOICES, default=12)
    title = models.CharField(max_length=180, blank=True, default="")
    subtitle = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    image = models.FileField(upload_to="site_kit/blocks/%Y/%m/", blank=True, null=True)
    image_url = models.URLField(blank=True, default="")
    bg_image = models.FileField(upload_to="site_kit/block_bg/%Y/%m/", blank=True, null=True)
    video_file = models.FileField(upload_to="site_kit/videos/%Y/%m/", blank=True, null=True)
    cta_label = models.CharField(max_length=80, blank=True, default="")
    cta_url = models.CharField(max_length=255, blank=True, default="")
    config_json = models.JSONField(default=dict, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.page.title} · {self.get_block_type_display()}"

    @property
    def resolved_image_url(self) -> str:
        if self.image:
            try:
                return self.image.url
            except Exception:
                pass
        return (self.image_url or "").strip()

    @property
    def resolved_video_url(self) -> str:
        if self.video_file:
            try:
                return self.video_file.url
            except Exception:
                pass
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        return (cfg.get("video_url") or "").strip()

    @property
    def grid_col_class(self) -> str:
        return {
            8: "col-12 col-lg-8",
            6: "col-12 col-lg-6",
            4: "col-12 col-md-6 col-lg-4",
            3: "col-12 col-md-6 col-lg-3",
        }.get(self.col_span, "col-12")

    @property
    def resolved_bg_image_url(self) -> str:
        if self.bg_image:
            try:
                return self.bg_image.url
            except Exception:
                pass
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        return (cfg.get("bg_image_url") or "").strip()

    @property
    def bg_color(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        val = (cfg.get("bg_color") or "").strip()
        if val.startswith("#") and len(val) in (4, 7):
            return val
        return ""

    @property
    def bg_overlay(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        val = (cfg.get("bg_overlay") or "dark").strip()
        return val if val in ("dark", "light", "none") else "dark"

    @property
    def is_dark_surface(self) -> bool:
        sv = self.style_variant
        return sv == "primary" or (sv == "bg_image" and self.bg_overlay == "dark")

    @property
    def style_variant(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        val = (cfg.get("style_variant") or "card").strip()
        return val if val in ("card", "solid", "flat", "flat_solid", "subtle", "primary", "bg_image") else "card"

    @property
    def card_wrapper_class(self) -> str:
        sv = self.style_variant
        if sv == "flat":
            return "py-2 h-100"
        if sv == "flat_solid":
            return "rounded-4 overflow-hidden h-100"
        if sv == "subtle":
            return "rounded-4 overflow-hidden border h-100"
        if sv == "primary":
            return "rounded-4 overflow-hidden shadow-sm text-white h-100"
        if sv == "bg_image":
            txt = "text-white" if self.bg_overlay == "dark" else "text-dark"
            return f"rounded-4 overflow-hidden shadow-sm {txt} h-100"
        return "card shadow-sm rounded-4 overflow-hidden h-100"

    @property
    def card_wrapper_style(self) -> str:
        sv = self.style_variant
        if sv == "solid":
            bg = self.bg_color or "var(--bs-body-bg, #ffffff)"
            return f"background: {bg} !important; backdrop-filter: none !important; -webkit-backdrop-filter: none !important;"
        if sv == "flat_solid":
            bg = self.bg_color or "var(--bs-body-bg, #ffffff)"
            return f"background: {bg} !important; box-shadow: none !important; backdrop-filter: none !important; -webkit-backdrop-filter: none !important;"
        if sv == "subtle":
            bg = self.bg_color or "color-mix(in srgb, var(--app-primary, #0d6efd) 10%, var(--bs-body-bg, #ffffff))"
            return f"background: {bg} !important; backdrop-filter: none !important; -webkit-backdrop-filter: none !important;"
        if sv == "primary":
            if self.bg_color:
                return f"background: {self.bg_color} !important; border: none !important;"
            return "background: var(--app-gradient, var(--app-primary, #0d6efd)) !important; border: none !important;"
        if sv == "bg_image":
            img = self.resolved_bg_image_url
            if img:
                safe_img = img.replace("'", "").replace('"', "")
                if self.bg_overlay == "dark":
                    ov = "linear-gradient(rgba(15, 23, 42, 0.56), rgba(15, 23, 42, 0.56)), "
                elif self.bg_overlay == "light":
                    ov = "linear-gradient(rgba(255, 255, 255, 0.72), rgba(255, 255, 255, 0.72)), "
                else:
                    ov = ""
                return f"background: {ov}url('{safe_img}') center / cover no-repeat !important;"
            return "background: var(--app-gradient, var(--app-primary, #0d6efd)) !important;"
        return ""

    @property
    def image_position(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        val = (cfg.get("image_position") or "right").strip()
        return val if val in ("right", "left", "top") else "right"

    @property
    def image_shape(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        val = (cfg.get("image_shape") or "rounded").strip()
        return val if val in ("rounded", "square", "circle") else "rounded"

    @property
    def image_aspect(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        val = (cfg.get("image_aspect") or "auto").strip()
        return val if val in ("auto", "landscape", "portrait", "square") else "auto"

    @property
    def image_shape_class(self) -> str:
        return {
            "square": "rounded-0 shadow-sm",
            "circle": "rounded-circle shadow-sm",
        }.get(self.image_shape, "rounded-4 shadow-sm")

    @property
    def image_aspect_style(self) -> str:
        return {
            "landscape": "aspect-ratio: 16 / 9; object-fit: cover; width: 100%;",
            "portrait": "aspect-ratio: 3 / 4; object-fit: cover; width: 100%;",
            "square": "aspect-ratio: 1 / 1; object-fit: cover; width: 100%;",
        }.get(self.image_aspect, "")

    @property
    def role_filter(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        return (cfg.get("role_filter") or "").strip()

    @property
    def category_filter(self) -> str:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        return (cfg.get("category_filter") or "").strip()

    @property
    def is_compact(self) -> bool:
        cfg = self.config_json if isinstance(self.config_json, dict) else {}
        return bool(cfg.get("compact"))
