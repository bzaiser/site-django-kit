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

    def test_redakteur_can_manage_pages_without_is_staff_and_cannot_edit_site_config(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group

        User = get_user_model()
        red_grp, _ = Group.objects.get_or_create(name="Redakteur")
        mem_grp, _ = Group.objects.get_or_create(name="Mitglied / Portal")
        red = User.objects.create_user(username="red_u", password="pw", is_staff=False)
        red.groups.add(red_grp)
        mem = User.objects.create_user(username="mem_u", password="pw", is_staff=False)
        mem.groups.add(mem_grp)

        self.client.force_login(mem)
        self.assertEqual(self.client.get(reverse("site_kit:manage")).status_code, 403)
        home_mem = self.client.get(reverse("site_kit:home"))
        self.assertContains(home_mem, "Mein Bereich")

        self.client.force_login(red)
        m_resp = self.client.get(reverse("site_kit:manage"))
        self.assertEqual(m_resp.status_code, 200)
        c_resp = self.client.post(reverse("site_kit:page_create"), {"title": "Redakteur-Seite", "is_published": "on"})
        self.assertEqual(c_resp.status_code, 302)
        self.assertTrue(PublicPage.objects.filter(title="Redakteur-Seite").exists())
        cfg_resp = self.client.post(reverse("site_kit:manage"), {"site_name": "Hack"})
        self.assertEqual(cfg_resp.status_code, 403)

    def test_lecturers_vita_courses_and_event_registration_capacity(self):
        from datetime import timedelta
        from django.utils import timezone
        from cal_kit.models import CalendarEvent
        from domain_kit.models import Person
        from list_kit.models import Collection, Entry
        from list_kit.services import seed_default_templates

        now = timezone.now()
        doz = Person.objects.create(
            first_name="Erika",
            last_name="Muster",
            is_public=True,
            website="https://erika.example.com",
            bio="Erfahrene Kursleiterin.\n- Zertifizierte Trainerin\n- 10 Jahre Praxis",
        )
        past_ev = CalendarEvent.objects.create(
            user=self.staff,
            title="Grundkurs 2025",
            start_at=now - timedelta(days=30),
            end_at=now - timedelta(days=30, hours=-2),
            is_public=True,
            instructor_ref=f"person:{doz.id}",
            instructor_name=doz.full_name,
        )
        future_ev = CalendarEvent.objects.create(
            user=self.staff,
            title="Aufbaukurs Python",
            start_at=now + timedelta(days=10),
            end_at=now + timedelta(days=10, hours=2),
            is_public=True,
            instructor_ref=f"person:{doz.id}",
            instructor_name=doz.full_name,
            allow_registration=True,
            max_participants=1,
        )
        tpls = {t.slug: t for t in seed_default_templates()}
        col = Collection.objects.create(
            title="Kursanmeldungen",
            template=tpls["kurs-anmeldung"],
            owner=self.staff,
            is_public=True,
            allow_public_submit=True,
        )
        Entry.objects.create(
            collection=col,
            title="Anmeldung 1",
            custom_data_json={"event_id": str(future_ev.id), "absender_email": "teilnehmer@example.com"},
        )

        r_doz = self.client.get(reverse("site_kit:lecturers_list"))
        self.assertEqual(r_doz.status_code, 200)
        self.assertContains(r_doz, "https://erika.example.com")
        self.assertContains(r_doz, "<li>Zertifizierte Trainerin</li>", html=True)
        self.assertContains(r_doz, "Grundkurs 2025")
        self.assertContains(r_doz, "Aufbaukurs Python")

        r_ev = self.client.get(reverse("site_kit:events_list"))
        self.assertContains(r_ev, "Ausgebucht (1/1)")
        r_reg = self.client.get(reverse("site_kit:event_registration") + f"?event={future_ev.id}")
        self.assertContains(r_reg, "ausgebucht")

    def test_grid_flat_block_wysiwyg_sanitizing_and_logo_upload(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from django.core.files.uploadedfile import SimpleUploadedFile

        User = get_user_model()
        red_grp, _ = Group.objects.get_or_create(name="Redakteur")
        red = User.objects.create_user(username="red_layout", password="pw", is_staff=False)
        red.groups.add(red_grp)

        p = PublicPage.objects.create(title="Layouttest", slug="layouttest", is_published=True)

        # Redakteur cannot add rich_html
        self.client.force_login(red)
        r_forbid = self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {"action": "add_block", "block_type": "rich_html", "body": "<p>Test</p>"},
        )
        self.assertEqual(r_forbid.status_code, 403)

        # Staff adds rich_html (with unsafe script/style) + flat 6/12 block
        self.client.force_login(self.staff)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "rich_html",
                "col_span": "6",
                "style_variant": "flat",
                "layout_fields_present": "1",
                "title": "HTML Box",
                "body": '<p class="text-primary" style="color:red" onclick="alert(1)">Hallo</p><script>bad()</script>',
            },
        )
        rich_b = p.blocks.get(title="HTML Box")
        self.assertEqual(rich_b.col_span, 6)
        self.assertEqual(rich_b.style_variant, "flat")
        self.assertIn('class="text-primary"', rich_b.body)
        self.assertNotIn("<script>", rich_b.body)
        self.assertNotIn("style=", rich_b.body)
        self.assertNotIn("onclick=", rich_b.body)

        # Redakteur CAN update text in existing rich_html block, CANNOT delete it
        self.client.force_login(red)
        r_edit = self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "edit_block",
                "block_id": rich_b.id,
                "title": "HTML Box",
                "body": '<p class="text-primary">Geändert</p>',
            },
        )
        self.assertEqual(r_edit.status_code, 302)
        rich_b.refresh_from_db()
        self.assertIn("Geändert", rich_b.body)
        self.assertEqual(
            self.client.post(
                reverse("site_kit:page_edit", args=[p.id]),
                {"action": "delete_block", "block_id": rich_b.id},
            ).status_code,
            403,
        )

        # Staff uploads logo
        self.client.force_login(self.staff)
        logo = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", content_type="image/png")
        self.client.post(reverse("site_kit:manage"), {"site_name": "TestVerein", "logo": logo, "container_width": "wide"})
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "col-lg-6")
        self.assertContains(pub, "container-xxl")
        self.assertContains(pub, "logo")

    def test_hero_image_shape_aspect_solid_and_people_role_filter(self):
        from domain_kit.models import Category, Person, Role

        r_vor = Role.objects.create(name="Vorstand")
        r_doz = Role.objects.create(name="Dozent")
        c_art = Category.objects.create(name="Kunst", purpose="person")
        p_vor = Person.objects.create(first_name="Viktor", last_name="Vorstand", is_public=True, category=c_art)
        p_vor.roles.add(r_vor)
        p_doz = Person.objects.create(first_name="Dora", last_name="Dozentin", is_public=True)
        p_doz.roles.add(r_doz)

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="DesignTest", slug="designtest", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "hero",
                "style_variant": "solid",
                "image_shape": "square",
                "image_aspect": "landscape",
                "image_url": "https://example.com/hero.jpg",
                "title": "Hero Solid",
            },
        )
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "people_grid",
                "role_filter": "Vorstand",
                "category_filter": "Kunst",
                "title": "Unser Vorstand",
            },
        )
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "rounded-0")
        self.assertContains(pub, "aspect-ratio: 16 / 9")
        self.assertContains(pub, "backdrop-filter: none")
        self.assertContains(pub, "Viktor")
        self.assertNotContains(pub, "Dora")

    def test_system_pages_news_and_termine_can_be_edited_with_blocks(self):
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("site_kit:system_page_edit", args=["termine"]))
        self.assertEqual(resp.status_code, 302)
        p = PublicPage.objects.get(slug="termine")
        self.assertFalse(p.show_in_nav)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {"action": "add_block", "block_type": "hero", "title": "Willkommen bei unseren Terminen"},
        )
        ev_page = self.client.get(reverse("site_kit:events_list"))
        self.assertContains(ev_page, "Willkommen bei unseren Terminen")


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
