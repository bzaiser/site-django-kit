import os
import sys

import django
from django.conf import settings
from django.core.management import call_command
from django.test import Client, TestCase

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV_ROOT = os.path.dirname(BASE_DIR)
for name in ("", "django-design-studio", "list-django-kit", "cal-django-kit", "domain-django-kit"):
    p = os.path.join(DEV_ROOT, name) if name else BASE_DIR
    if p not in sys.path and os.path.isdir(p):
        sys.path.insert(0, p)

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-site-kit",
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "django.contrib.sessions",
            "django.contrib.messages",
            "django.contrib.staticfiles",
            "design_studio",
            "list_kit",
            "cal_kit",
            "domain_kit",
            "site_kit",
        ],
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.common.CommonMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.messages.middleware.MessageMiddleware",
        ],
        ROOT_URLCONF="tests.test_site_kit",
        TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": True,
            "OPTIONS": {
                "context_processors": [
                    "django.template.context_processors.request",
                    "django.contrib.auth.context_processors.auth",
                    "django.contrib.messages.context_processors.messages",
                    "design_studio.context_processors.theme_context",
                ],
            },
        }],
        STATIC_URL="/static/",
        USE_TZ=True,
        TIME_ZONE="Europe/Berlin",
    )
    django.setup()

from django.contrib.auth import get_user_model
from django.urls import include, path, reverse
from django.utils import timezone
from datetime import timedelta
from cal_kit.models import CalendarEvent
from domain_kit.models import Person
from list_kit.models import Collection, Entry, ListTemplate
from site_kit.models import PageBlock, PublicPage, SiteConfig

urlpatterns = [
    path("lists/", include("list_kit.urls")),
    path("calendar/", include("cal_kit.urls")),
    path("domain/", include("domain_kit.urls")),
    path("design/", include("design_studio.urls")),
    path("", include("site_kit.urls")),
]


class SiteKitTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user(username="admin", password="pw", is_staff=True)
        self.client = Client()

    def test_no_missing_migrations(self):
        call_command("makemigrations", "site_kit", check=True, dry_run=True, verbosity=0)

    def test_public_pages_news_events_legal_sitemap_and_rss(self):
        SiteConfig.objects.create(pk=1, site_name="SV Test e.V.", imprint_text="Musterstr. 1", privacy_text="DSGVO Info")
        home = PublicPage.objects.create(title="Startseite", slug="start", is_home=True, is_published=True)
        PageBlock.objects.create(page=home, block_type="hero", title="Willkommen beim SV Test", cta_label="News", cta_url="/news/", position=1)
        PageBlock.objects.create(page=home, block_type="news_feed", title="Aktuelles", position=2)
        PageBlock.objects.create(page=home, block_type="upcoming_events", title="Termine", position=3)
        PageBlock.objects.create(page=home, block_type="people_grid", title="Vorstand", position=4)

        news_tpl = ListTemplate.objects.create(name="News", slug="news", content_type="news")
        news_col = Collection.objects.create(title="Vereinsnews", template=news_tpl, owner=self.staff, is_public=True)
        entry = Entry.objects.create(collection=news_col, title="Sommerfest 2026", notes="**Alle** sind eingeladen!", visibility="public")

        now = timezone.now()
        CalendarEvent.objects.create(user=self.staff, title="Jahreshauptversammlung", start_at=now + timedelta(days=3), end_at=now + timedelta(days=3, hours=2), is_public=True)
        Person.objects.create(first_name="Max", last_name="Vorstand", job_title="1. Vorsitzender", is_public=True)

        r_home = self.client.get(reverse("site_kit:home"))
        self.assertEqual(r_home.status_code, 200)
        self.assertContains(r_home, "Willkommen beim SV Test")
        self.assertContains(r_home, "Sommerfest 2026")
        self.assertContains(r_home, "Jahreshauptversammlung")
        self.assertContains(r_home, "Max")

        r_news = self.client.get(reverse("site_kit:news_detail", args=[entry.slug]))
        self.assertEqual(r_news.status_code, 200)
        self.assertContains(r_news, "<strong>Alle</strong>")

        self.assertEqual(self.client.get(reverse("site_kit:events_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_kit:imprint")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_kit:privacy")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_kit:sitemap")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_kit:robots")).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_kit:news_rss")).status_code, 200)

    def test_staff_manage_page_and_block_crud(self):
        self.client.force_login(self.staff)
        c_resp = self.client.post(reverse("site_kit:page_create"), {"title": "Abteilungen", "is_published": "on", "show_in_nav": "on"})
        self.assertEqual(c_resp.status_code, 302)
        p = PublicPage.objects.get(title="Abteilungen")
        b_resp = self.client.post(reverse("site_kit:page_edit", args=[p.id]), {
            "action": "add_block",
            "block_type": "text_image",
            "title": "Fußball",
            "body": "Training dienstags",
        })
        self.assertRedirects(b_resp, reverse("site_kit:page_edit", args=[p.id]))
        self.assertTrue(p.blocks.filter(title="Fußball").exists())
        d_resp = self.client.post(reverse("site_kit:page_delete", args=[p.id]))
        self.assertEqual(d_resp.status_code, 302)
        self.assertFalse(PublicPage.objects.filter(pk=p.id).exists())

    def test_text_image_block_supports_uploaded_image_and_url(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Bildtest", slug="bildtest", is_published=True)
        img = SimpleUploadedFile("demo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", content_type="image/png")
        b_resp = self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "text_image",
                "title": "Mit Bild",
                "body": "Beschreibung",
                "image": img,
            },
        )
        self.assertRedirects(b_resp, reverse("site_kit:page_edit", args=[p.id]))
        block = p.blocks.get(title="Mit Bild")
        self.assertTrue(bool(block.image))
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, block.resolved_image_url)

    def test_events_list_filters_by_public_calendar_category(self):
        from datetime import timedelta
        from django.utils import timezone
        from cal_kit.models import CalendarCategory, CalendarEvent

        now = timezone.now()
        cat_events = CalendarCategory.objects.create(user=self.staff, name="Veranstaltungen", color_state=1, is_shared=True)
        cat_courses = CalendarCategory.objects.create(user=self.staff, name="Kurse", color_state=2, is_shared=True)
        CalendarEvent.objects.create(
            user=self.staff,
            title="Sommerfest",
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=3),
            category=cat_events,
            is_public=True,
        )
        CalendarEvent.objects.create(
            user=self.staff,
            title="Yoga-Kurs",
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=1),
            category=cat_courses,
            is_public=True,
        )
        all_resp = self.client.get(reverse("site_kit:events_list"))
        self.assertEqual(all_resp.status_code, 200)
        self.assertContains(all_resp, "Sommerfest")
        self.assertContains(all_resp, "Yoga-Kurs")
        self.assertContains(all_resp, "Veranstaltungen")
        self.assertContains(all_resp, "Kurse")

        filtered_resp = self.client.get(reverse("site_kit:events_list") + f"?kalender={cat_courses.pk}")
        self.assertEqual(filtered_resp.status_code, 200)
        self.assertContains(filtered_resp, "Yoga-Kurs")
        self.assertNotContains(filtered_resp, "Sommerfest")


if __name__ == "__main__":
    import unittest
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment

    setup_test_environment()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SiteKitTests)
    runner = DiscoverRunner(verbosity=2)
    old = runner.setup_databases()
    result = runner.run_suite(suite)
    runner.teardown_databases(old)
    teardown_test_environment()
    sys.exit(0 if result.wasSuccessful() else 1)
