import atexit
import os
import shutil
import sys
import tempfile

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
    _TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="site_kit_test_media_")
    atexit.register(shutil.rmtree, _TEST_MEDIA_ROOT, ignore_errors=True)
    settings.configure(
        DEBUG=True,
        MEDIA_ROOT=_TEST_MEDIA_ROOT,
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
            recurrence_rule="weekly",
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
                "body": '<p class="text-primary" style="position:fixed; background-image:url(javascript:alert(1))" onclick="alert(1)">Hallo</p><script>bad()</script>',
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

    def test_redakteur_cockpit_and_quick_news(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from list_kit.models import Entry

        User = get_user_model()
        red_grp, _ = Group.objects.get_or_create(name="Redakteur")
        red = User.objects.create_user(username="red_cockpit", password="pw", is_staff=False)
        red.groups.add(red_grp)

        self.client.force_login(red)
        r_get = self.client.get(reverse("site_kit:cockpit"))
        self.assertEqual(r_get.status_code, 200)
        self.assertContains(r_get, "Redaktions-Cockpit")

        r_post = self.client.post(
            reverse("site_kit:cockpit"),
            {"action": "quick_news", "title": "Cockpit Eilmeldung", "notes": "Text"},
        )
        self.assertEqual(r_post.status_code, 302)
        self.assertTrue(Entry.objects.filter(title="Cockpit Eilmeldung").exists())

    def test_rich_html_preserves_color_link_in_editor_and_stats_bar_renders(self):
        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="BlockTest", slug="blocktest", is_published=True)

        # Add rich_html block with inline color, link, and unsafe style attempt
        r_rich = self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "rich_html",
                "col_span": "12",
                "title": "Farbtest",
                "body": '<p><span style="color: #e11d48; background-image: url(javascript:alert(1))">Rot</span> <a href="https://example.org">Link</a></p>',
            },
        )
        self.assertEqual(r_rich.status_code, 302)
        rb = PageBlock.objects.get(page=p, block_type="rich_html")
        self.assertIn("color: #e11d48", rb.body)
        self.assertNotIn("javascript:", rb.body)
        self.assertIn('href="https://example.org"', rb.body)

        # Add stats_bar & public_form without extra config
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {"action": "add_block", "block_type": "stats_bar", "col_span": "12", "body": "42 | Mitglieder"},
        )
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {"action": "add_block", "block_type": "public_form", "col_span": "12", "title": "Kontaktformular"},
        )

        # Editor modal must include Link, color picker, and style-insert
        ed = self.client.get(reverse("site_kit:page_edit", args=[p.id]))
        self.assertContains(ed, f'data-wysiwyg-toolbar="edit-{rb.id}"')
        self.assertContains(ed, f'data-style-insert="edit-{rb.id}"')
        self.assertContains(ed, 'data-cmd-color="foreColor"')

        # Public page renders color, stats_bar, and public_form fallback/form
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "color: #e11d48")
        self.assertContains(pub, "42")
        self.assertContains(pub, "Mitglieder")
        self.assertContains(pub, "Kontaktformular")

    def test_person_detail_and_annual_program_views_and_pdf(self):
        from cal_kit.models import CalendarEvent, OfferingTemplate
        from domain_kit.models import Person

        p = Person.objects.create(
            first_name="Ursula",
            last_name="Wolke",
            job_title="Aquarell-Dozentin",
            bio="Studium der Malerei.\n- Schwerpunkt Landschaft",
            is_public=True,
        )
        r_pers = self.client.get(reverse("site_kit:person_detail", args=[p.id]))
        self.assertEqual(r_pers.status_code, 200)
        self.assertContains(r_pers, "Ursula Wolke")
        self.assertContains(r_pers, "Schwerpunkt Landschaft")

        tpl = OfferingTemplate.objects.create(
            user=self.staff,
            title="Aquarellkurs",
            instructor_ref=f"person:{p.id}",
            instructor_name="Ursula Wolke",
            description="Lichtdurchflutetes Aquarell.",
            fee_amount=35,
            show_in_program=True,
        )
        CalendarEvent.objects.create(
            user=self.staff,
            offering_template=tpl,
            title="Aquarellkurs",
            start_at=timezone.now() + timedelta(days=10),
            end_at=timezone.now() + timedelta(days=10, hours=3),
            is_public=True,
            allow_registration=True,
        )

        prog = self.client.get(reverse("site_kit:program_detail", args=["jahresprogramm"]))
        self.assertEqual(prog.status_code, 200)
        self.assertContains(prog, "Aquarellkurs")
        self.assertContains(prog, reverse("site_kit:person_detail", args=[p.id]))

        pdf_r = self.client.get(reverse("site_kit:annual_program_pdf"))
        self.assertEqual(pdf_r.status_code, 200)

    def test_people_grid_photo_width_style_and_compact_mode(self):
        from domain_kit.models import Person

        Person.objects.create(
            first_name="Klara",
            last_name="Vorstand",
            job_title="1. Vorsitzende",
            bio="Lange Vita die im Kompaktmodus nicht erscheint.",
            is_public=True,
        )
        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Team", slug="team-grid", is_published=True)
        r = self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "people_grid",
                "col_span": "12",
                "title": "Vorstand",
                "people_layout_present": "1",
                "layout_fields_present": "1",
                "person_col_span": "6",
                "person_card_style": "horizontal",
                "show_photos": "on",
                "compact": "on",
            },
        )
        self.assertEqual(r.status_code, 302)
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "col-md-6")
        self.assertContains(pub, "bi-person-fill")
        self.assertContains(pub, "Klara Vorstand")
        self.assertNotContains(pub, "Lange Vita die im Kompaktmodus nicht erscheint.")

    def test_news_archive_shows_expired_news_and_link_from_news_list(self):
        from list_kit.models import Collection, Entry
        from list_kit.services import seed_default_templates

        seed_default_templates()
        from list_kit.models import ListTemplate
        tpl = ListTemplate.objects.filter(content_type="news").first()
        col = Collection.objects.create(title="News", template=tpl, owner=self.staff, is_public=True)
        Entry.objects.create(collection=col, title="Frische News", visibility="public")
        old_e = Entry.objects.create(
            collection=col,
            title="Alte News",
            visibility="public",
            expires_at=timezone.now() - timedelta(days=2),
        )

        r_list = self.client.get(reverse("site_kit:news_list"))
        self.assertContains(r_list, "Frische News")
        self.assertNotContains(r_list, "Alte News")
        self.assertContains(r_list, reverse("site_kit:news_archive"))

        r_arch = self.client.get(reverse("site_kit:news_archive"))
        self.assertContains(r_arch, "Alte News")
        self.assertNotContains(r_arch, "Frische News")

        r_det = self.client.get(reverse("site_kit:news_detail", args=[old_e.slug]))
        self.assertEqual(r_det.status_code, 200)
        self.assertContains(r_det, "Archivierte Meldung")

    def test_video_and_photo_gallery_blocks(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from domain_kit.models import Person
        from list_kit.models import Collection, Entry, EntryImage, ListTemplate
        from list_kit.services import seed_default_templates

        seed_default_templates()
        tpl = ListTemplate.objects.get(slug="fotoalbum")
        col = Collection.objects.create(title="Vernissage", template=tpl, owner=self.staff, is_public=True)
        entry = Entry.objects.create(collection=col, title="Eröffnung", visibility="public")
        tiny_png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        img = EntryImage.objects.create(
            entry=entry,
            image=SimpleUploadedFile("vernissage.png", tiny_png, content_type="image/png"),
            caption="Saalansicht",
        )
        pers = Person.objects.create(first_name="Max", last_name="Maler", is_public=True)

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Medien", slug="medien", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "video",
                "col_span": "12",
                "title": "Atelier-Video",
                "video_url": "https://youtu.be/dQw4w9WgXcQ",
            },
        )
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "photo_gallery",
                "col_span": "12",
                "title": "Impressionen",
                "collection_slug": col.slug,
                "gallery_layout": "grid",
                "gallery_cols": "3",
                "linked_person_id": str(pers.id),
            },
        )

        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "youtube-nocookie.com/embed/dQw4w9WgXcQ")
        self.assertContains(pub, "loadPrivacyVideo")
        self.assertContains(pub, "Saalansicht")
        self.assertContains(pub, "Max Maler")
        img.image.delete(save=False)

    def test_multi_programs_crud_blocks_nav_and_public_views(self):
        from cal_kit.models import OfferingTemplate, Program

        self.client.force_login(self.staff)
        r_create = self.client.post(
            reverse("site_kit:program_manage"),
            {
                "action": "create",
                "title": "Kinderferienprogramm",
                "description": "Kreative Ferientage für Kinder",
                "card_mode": "compact_uniform",
                "show_in_nav": "on",
                "is_public": "on",
                "is_featured": "on",
                "position": "10",
            },
        )
        self.assertEqual(r_create.status_code, 302)
        prog = Program.objects.get(title="Kinderferienprogramm")
        self.assertEqual(prog.slug, "kinderferienprogramm")
        self.assertTrue(prog.is_featured)

        OfferingTemplate.objects.create(
            user=self.staff,
            title="Regulärer Malkurs",
            show_in_program=True,
            is_active=True,
        )
        OfferingTemplate.objects.create(
            user=self.staff,
            title="Ferien-Töpfern",
            program=prog,
            show_in_program=True,
            is_active=True,
        )
        OfferingTemplate.objects.create(
            user=self.staff,
            title="Archiv-Ferienkurs",
            program=prog,
            show_in_program=True,
            is_active=False,
        )

        # Hub page at /programm/ shows featured program & link to /programm/standard/
        r_hub = self.client.get(reverse("site_kit:annual_program"))
        self.assertEqual(r_hub.status_code, 200)
        self.assertContains(r_hub, "Angebote &amp; Programme")
        self.assertContains(r_hub, "Kinderferienprogramm")
        self.assertContains(r_hub, "/programm/standard/")
        # Sub-program sits under parent_key='programm' (dropdown) so top nav is not overloaded
        from site_kit.views import get_web_menu_studio_context
        nav_map = {item["key"]: item for item in get_web_menu_studio_context()["web_menu_studio_items"]}
        self.assertEqual(nav_map["program:kinderferienprogramm"]["parent_key"], "programm")

        # Hub layout switching (carousel, compact, cards_small)
        for layout_mode in ("carousel", "compact", "cards_small"):
            self.client.post(
                reverse("site_kit:program_manage"),
                {"action": "set_hub_layout", "program_hub_layout": layout_mode},
            )
            r_hl = self.client.get(reverse("site_kit:annual_program"))
            self.assertContains(r_hl, f'data-hub-layout="{layout_mode}"')

        # /programm/standard/ redirects to /programm/jahresprogramm/ showing default offerings
        r_std = self.client.get(reverse("site_kit:program_standard"), follow=True)
        self.assertEqual(r_std.status_code, 200)
        self.assertContains(r_std, "Regulärer Malkurs")
        self.assertNotContains(r_std, "Ferien-Töpfern")

        # Custom program shows only its active offerings + compact_uniform + description
        r_prog = self.client.get(reverse("site_kit:program_detail", args=[prog.slug]))
        self.assertEqual(r_prog.status_code, 200)
        self.assertContains(r_prog, "Ferien-Töpfern")
        self.assertContains(r_prog, "Kreative Ferientage für Kinder")
        self.assertContains(r_prog, 'data-card-mode="compact_uniform"')
        self.assertNotContains(r_prog, "Regulärer Malkurs")
        self.assertNotContains(r_prog, "Archiv-Ferienkurs")

        # PDF view for custom program
        r_pdf = self.client.get(reverse("site_kit:program_detail_pdf", args=[prog.slug]))
        self.assertEqual(r_pdf.status_code, 200)

        # Blocks page creation for custom program
        r_blk = self.client.get(reverse("site_kit:program_blocks_edit", args=[prog.id]))
        self.assertEqual(r_blk.status_code, 302)
        self.assertTrue(PublicPage.objects.filter(slug="programm-kinderferienprogramm").exists())

        # /seite/programm-<slug>/ redirects to /programm/<slug>/ and is hidden from normal pages list in manage
        r_redir = self.client.get(reverse("site_kit:page_detail", args=["programm-kinderferienprogramm"]))
        self.assertRedirects(r_redir, reverse("site_kit:program_detail", args=["kinderferienprogramm"]), fetch_redirect_response=False)
        r_mgr = self.client.get(reverse("site_kit:manage"))
        self.assertNotContains(r_mgr, "/seite/programm-kinderferienprogramm/")

        # Renaming program title & slug updates URL and associated PublicPage
        self.client.post(
            reverse("site_kit:program_manage"),
            {
                "action": "update",
                "program_id": str(prog.id),
                "title": "Ferienprogramm Sommer",
                "slug": "ferien-sommer",
                "card_mode": "compact_uniform",
                "show_in_nav": "on",
                "is_public": "on",
                "is_featured": "on",
                "position": "10",
            },
        )
        prog.refresh_from_db()
        self.assertEqual(prog.slug, "ferien-sommer")
        self.assertTrue(PublicPage.objects.filter(slug="programm-ferien-sommer").exists())
        r_new = self.client.get(reverse("site_kit:program_detail", args=["ferien-sommer"]))
        self.assertEqual(r_new.status_code, 200)
        self.assertContains(r_new, "Ferien-Töpfern")
        self.assertContains(r_new, 'href="/programm/ferien-sommer/"')

    def test_events_list_settings_hide_description_by_default_and_group_toggle(self):
        from cal_kit.models import CalendarEvent

        CalendarEvent.objects.create(
            user=self.staff,
            title="Atelier-Abend",
            description="Ganz langer Beschreibungstext der nur bei Bedarf gezeigt wird.",
            start_at=timezone.now() + timedelta(days=3),
            end_at=timezone.now() + timedelta(days=3, hours=2),
            is_public=True,
        )
        r_def = self.client.get(reverse("site_kit:events_list"))
        self.assertContains(r_def, "Atelier-Abend")
        self.assertNotContains(r_def, "Ganz langer Beschreibungstext")

        self.client.force_login(self.staff)
        self.client.post(
            reverse("site_kit:manage"),
            {
                "action": "save_events_settings",
                "events_layout": "cards",
                "events_image_position": "none",
                "events_show_description": "on",
            },
        )
        r_on = self.client.get(reverse("site_kit:events_list"))
        self.assertContains(r_on, "Ganz langer Beschreibungstext")
        self.assertContains(r_on, "Kommende Termine")

    def test_people_grid_multi_filters_and_matrix_image_position(self):
        from domain_kit.models import Category, Person, Role

        r_vor = Role.objects.create(name="Vorstand")
        r_bei = Role.objects.create(name="Beirat")
        r_mit = Role.objects.create(name="Mitglied")
        c_kun = Category.objects.create(name="Kunst", purpose="person")
        c_mus = Category.objects.create(name="Musik", purpose="person")

        p1 = Person.objects.create(first_name="Anna", last_name="Vor", category=c_kun, is_public=True)
        p1.roles.add(r_vor)
        p2 = Person.objects.create(first_name="Ben", last_name="Bei", category=c_mus, is_public=True)
        p2.roles.add(r_bei)
        p3 = Person.objects.create(first_name="Carl", last_name="Mit", category=c_kun, is_public=True)
        p3.roles.add(r_mit)

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Team Matrix", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "people_grid",
                "people_layout_present": "1",
                "role_filters": ["Vorstand", "Beirat"],
                "category_filters": ["Kunst", "Musik"],
                "person_card_style": "horizontal",
                "image_position": "bottom-left",
                "title": "Team",
            },
        )
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "Anna Vor")
        self.assertContains(pub, "Ben Bei")
        self.assertNotContains(pub, "Carl Mit")
        self.assertContains(pub, "align-items-end")

    def test_col_span_9_and_design_studio_bootstrap_semantic_vars(self):
        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Grid 9/12", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {"action": "add_block", "block_type": "news_feed", "col_span": "9", "title": "News 9/12"},
        )
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "col-lg-9")
        for role in ("primary", "secondary", "success", "info", "warning", "danger"):
            self.assertContains(pub, f"--bs-{role}-rgb:")

    def test_news_feed_compact_single_line_and_image_fit(self):
        from list_kit.models import Collection, Entry
        from list_kit.services import seed_default_templates

        tpl = next(t for t in seed_default_templates() if t.slug == "news-aktuelles")
        col = Collection.objects.create(title="News", slug="news-compact-test", template=tpl, owner=self.staff, is_public=True)
        long_text = "A" * 130
        Entry.objects.create(
            collection=col,
            title="Kurze Meldung",
            notes=f"<p>{long_text}</p>",
            image_url="https://example.com/hochformat.jpg",
            visibility="public",
            created_by=self.staff,
        )

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="News Kompakt", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "news_feed",
                "news_options_present": "1",
                "news_density": "compact",
                "news_image_fit": "contain",
                "show_news_images": "on",
                "title": "Kompakt News",
            },
        )
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "Details ansehen")
        self.assertContains(pub, "object-fit-contain")
        self.assertNotContains(pub, long_text)

        blk = p.blocks.first()
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "edit_block",
                "block_id": str(blk.id),
                "block_type": "news_feed",
                "news_options_present": "1",
                "news_density": "single_line",
                "title": "Einzeilig News",
            },
        )
        pub2 = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub2, "Kurze Meldung")
        self.assertContains(pub2, "Details ansehen")
        self.assertNotContains(pub2, "hochformat.jpg")

    def test_quick_links_block_internal_external_icons_and_layouts(self):
        import json

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Schnellzugriffe", is_published=True)
        links = [
            {"label": "Termine", "subtitle": "Alle Events", "url": "/termine/", "icon": "bi-calendar-event", "accent": "primary", "new_tab": False},
            {"label": "Partner", "subtitle": "", "url": "https://example.org", "icon": "bi-box-arrow-up-right", "accent": "success", "new_tab": True},
        ]
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "quick_links",
                "quick_links_present": "1",
                "links_layout": "grid_3",
                "links_style": "cards",
                "show_icons": "on",
                "quick_links_json": json.dumps(links),
                "title": "Direkt zu",
            },
        )
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "Direkt zu")
        self.assertContains(pub, "bi-calendar-event")
        self.assertContains(pub, 'href="https://example.org"')
        self.assertContains(pub, 'target="_blank"')
        self.assertContains(pub, "col-lg-4")

        blk = p.blocks.first()
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "edit_block",
                "block_id": str(blk.id),
                "block_type": "quick_links",
                "quick_links_present": "1",
                "links_layout": "inline",
                "links_style": "pills",
                "quick_links_json": json.dumps(links),
                "title": "Direkt zu",
            },
        )
        pub2 = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub2, "btn-outline-success")
        self.assertNotContains(pub2, "bi-calendar-event")

    def test_public_events_list_year_filter(self):
        from cal_kit.models import CalendarEvent

        CalendarEvent.objects.create(
            user=self.staff,
            title="Frühjahrsfest 2027",
            start_at="2027-04-15T14:00Z",
            end_at="2027-04-15T17:00Z",
            is_public=True,
        )
        r_2027 = self.client.get(reverse("site_kit:events_list") + "?jahr=2027")
        self.assertEqual(r_2027.status_code, 200)
        self.assertContains(r_2027, "Frühjahrsfest 2027")
        self.assertContains(r_2027, "?jahr=2027")

    def test_public_form_route_and_quick_link_targets_include_mitgliedsantrag(self):
        from list_kit.models import Collection
        from list_kit.services import seed_default_templates

        tpl = next(t for t in seed_default_templates() if t.slug == "mitgliedsantrag")
        tpl.description = "An: {{ vereinsname }}\n{{ bankverbindungen }}"
        tpl.save(update_fields=["description"])
        Collection.objects.get_or_create(
            slug="mitgliedsantrag",
            defaults={"title": "Mitgliedsantrag", "template": tpl, "owner": self.staff, "is_public": True, "allow_public_submit": True},
        )
        from design_studio.models import BrandingProfile
        BrandingProfile.objects.update_or_create(pk=1, defaults={"org_name": "Kunstverein Test e.V.", "iban": "DE1234567890", "bic": "TESTBIC"})
        r_form = self.client.get(reverse("site_kit:public_form", args=["mitgliedsantrag"]))
        self.assertEqual(r_form.status_code, 200)
        self.assertContains(r_form, "Mitgliedsantrag")
        self.assertContains(r_form, "Kunstverein Test e.V.")
        self.assertContains(r_form, "DE1234567890")
        self.assertContains(r_form, "Formular drucken")

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Start Test", is_published=True)
        r_ed = self.client.get(reverse("site_kit:page_edit", args=[p.id]))
        self.assertEqual(r_ed.status_code, 200)
        self.assertContains(r_ed, "/formular/mitgliedsantrag/")

    def test_block_editor_shows_remove_image_and_clears_image_and_bg_image(self):
        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Bild Entfernen", is_published=True)
        blk = PageBlock.objects.create(
            page=p,
            block_type="hero",
            title="Hero",
            image_url="https://example.com/hero.jpg",
            config_json={"bg_image_url": "https://example.com/bg.jpg"},
        )
        r_ed = self.client.get(reverse("site_kit:page_edit", args=[p.id]))
        self.assertContains(r_ed, f'id="editRemoveImg-{blk.id}"')
        self.assertContains(r_ed, f'id="rmBgImg{blk.id}"')

        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "edit_block",
                "block_id": str(blk.id),
                "block_type": "hero",
                "title": "Hero",
                "remove_image": "1",
                "remove_bg_image": "1",
            },
        )
        blk.refresh_from_db()
        self.assertEqual(blk.resolved_image_url, "")
        self.assertEqual(blk.config_json.get("bg_image_url", ""), "")

    def test_block_bg_image_renders_for_all_style_variants(self):
        p = PublicPage.objects.create(title="BG Styles", is_published=True)
        for style in ("card", "solid", "flat", "subtle", "primary"):
            PageBlock.objects.all().delete()
            PageBlock.objects.create(
                page=p,
                block_type="hero",
                title=f"Hero {style}",
                config_json={"style_variant": style, "bg_image_url": "https://example.com/banner.jpg", "bg_overlay": "dark"},
            )
            pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
            self.assertContains(pub, "https://example.com/banner.jpg")

    def test_collection_list_block_and_entry_detail_security(self):
        from list_kit.models import Collection, Entry
        from list_kit.services import seed_default_templates

        tpl_map = {t.slug: t for t in seed_default_templates()}
        pub_col = Collection.objects.create(title="Projekte", slug="projekte-pub", template=tpl_map["aufgabenliste"], owner=self.staff, is_public=True)
        e_pub = Entry.objects.create(
            collection=pub_col,
            title="Skulpturenpark",
            notes="<p>" + ("B" * 130) + "</p>",
            image_url="https://example.com/park.jpg",
            visibility="team",
            created_by=self.staff,
        )
        e_priv = Entry.objects.create(collection=pub_col, title="Geheim", visibility="private", created_by=self.staff)

        inbox_col = Collection.objects.create(title="Anträge", slug="antraege-sec", template=tpl_map["mitgliedsantrag"], owner=self.staff, is_public=True)
        e_inbox = Entry.objects.create(collection=inbox_col, title="Vertraulicher Antrag", created_by=self.staff)

        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="Listen Block", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "collection_list",
                "collection_slug": "projekte-pub",
                "news_options_present": "1",
                "news_layout": "grid",
                "news_density": "compact",
                "news_image_fit": "contain",
                "show_news_images": "on",
                "title": "Unsere Projekte",
            },
        )
        self.client.logout()
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "Skulpturenpark")
        self.assertContains(pub, "Details ansehen")
        self.assertContains(pub, "object-fit-contain")
        self.assertNotContains(pub, "Geheim")

        self.assertEqual(self.client.get(reverse("site_kit:collection_entry_detail", args=[e_pub.id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("site_kit:collection_entry_detail", args=[e_priv.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("site_kit:collection_entry_detail", args=[e_inbox.id])).status_code, 404)

        # Full mode also renders Details ansehen button
        p_full = PublicPage.objects.create(title="CLFull", is_published=True)
        PageBlock.objects.create(page=p_full, block_type="collection_list", title="Listen", config_json={"collection_slug": "projekte-pub", "news_density": "full"})
        self.assertContains(self.client.get(reverse("site_kit:page_detail", args=[p_full.slug])), "Details ansehen")

    def test_public_form_renders_radio_select_and_multiselect_options(self):
        from list_kit.models import Collection, ListTemplate

        tpl = ListTemplate.objects.create(
            name="Mitgliedsantrag Optionen",
            slug="mitgliedsantrag-opts-tpl",
            description="Aufnahme in den [[ORG_NAME]].",
            content_type="form_inbox",
            custom_fields_json=[
                {
                    "key": "mitgliedsart",
                    "label": "Mitgliedschaft (bitte wählen)",
                    "type": "radio",
                    "options": "Aktives Mitglied (62 Euro), Fördermitglied (31 Euro), Familienmitglied (93 Euro)",
                    "zone": "body",
                    "show_on_card": True,
                    "section": "Mitgliedschaft",
                },
                {
                    "key": "nachname",
                    "label": "Name",
                    "type": "text",
                    "options": "",
                    "zone": "hidden",
                    "show_on_card": False,
                    "width": "half",
                },
                {
                    "key": "portal_zugang",
                    "label": "Portal-Zugang gewünscht?",
                    "type": "select",
                    "options": "Ja, Nein",
                    "zone": "body",
                    "show_on_card": True,
                },
                {
                    "key": "interessen",
                    "label": "Interessen",
                    "type": "multiselect",
                    "options": "Malerei, Grafik",
                    "zone": "body",
                    "show_on_card": True,
                },
            ],
        )
        col = Collection.objects.create(
            title="Mitgliedsantrag",
            slug="mitgliedsantrag-opts",
            template=tpl,
            owner=self.staff,
            is_public=True,
            allow_public_submit=True,
        )
        res = self.client.get(reverse("site_kit:public_form", args=[col.slug]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'type="radio"')
        self.assertContains(res, "Aktives Mitglied (62 Euro)")
        self.assertContains(res, "Fördermitglied (31 Euro)")
        self.assertContains(res, "Familienmitglied (93 Euro)")
        self.assertContains(res, '<option value="Ja">Ja</option>')
        self.assertContains(res, '<option value="Nein">Nein</option>')
        self.assertContains(res, 'value="Malerei"')
        self.assertContains(res, 'value="Grafik"')
        self.assertContains(res, 'Mitgliedschaft')
        self.assertContains(res, 'col-md-6')
        self.assertContains(res, 'custom_nachname')
        self.assertContains(res, 'Aufnahme in den Mein Verein.')
        self.assertNotContains(res, '[[ORG_NAME]]')
        self.assertContains(res, 'd-print-flex')

    def test_video_block_padding_autoplay_and_filename_display(self):
        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="VideoOpts", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "video",
                "title": "Clip",
                "video_url": "https://example.com/clip.mp4",
                "video_padding": "none",
                "video_autoplay": "1",
            },
        )
        self.client.logout()
        pub = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub, "card-body p-0")
        self.assertContains(pub, "autoplay muted loop playsinline")

    def test_people_grid_ordering_options(self):
        from domain_kit.models import Person
        Person.objects.create(first_name="Zoe", last_name="Alpha", is_public=True)
        Person.objects.create(first_name="Anna", last_name="Zeta", is_public=True)
        self.client.force_login(self.staff)
        p = PublicPage.objects.create(title="PeopleOrder", is_published=True)
        self.client.post(
            reverse("site_kit:page_edit", args=[p.id]),
            {
                "action": "add_block",
                "block_type": "people_grid",
                "title": "Team",
                "people_layout_present": "1",
                "people_layout": "carousel",
                "people_order": "first_name",
            },
        )
        self.client.logout()
        html = self.client.get(reverse("site_kit:page_detail", args=[p.slug])).content.decode()
        self.assertLess(html.index("Anna Zeta"), html.index("Zoe Alpha"))

        # Edit modal for collection_list includes unified image_shape, image_aspect, and limit fields
        self.client.force_login(self.staff)
        p_cl = PublicPage.objects.create(title="CLParity", is_published=True)
        PageBlock.objects.create(page=p_cl, block_type="collection_list", title="Listen", config_json={"collection_slug": ""})
        ed = self.client.get(reverse("site_kit:page_edit", args=[p_cl.id]))
        self.assertContains(ed, 'name="image_shape"')
        self.assertContains(ed, 'name="image_aspect"')
        self.assertContains(ed, 'name="news_layout"')
        self.assertContains(ed, 'name="limit"')

    def test_separate_locked_website_theme_independent_from_admin_app_theme(self):
        from site_kit.models import SiteConfig

        self.client.force_login(self.staff)
        # 1. Set admin app theme to #118844 and lock it for the public website
        self.client.post(reverse("design_studio:save"), {"gradient_start": "#118844", "gradient_end": "#0d6efd", "theme_mood": "modern"})
        r_lock = self.client.post(reverse("design_studio:website_theme_action"), {"website_theme_action": "lock"})
        self.assertEqual(r_lock.status_code, 302)
        sc = SiteConfig.get_solo()
        self.assertTrue(sc.website_theme_locked)

        # 2. Change admin app theme to #cc2255
        self.client.post(reverse("design_studio:save"), {"gradient_start": "#cc2255", "gradient_end": "#0d6efd", "theme_mood": "modern"})
        self.client.logout()

        # Public website still renders locked #118844, not #cc2255
        p = PublicPage.objects.create(title="ThemeLock", is_published=True)
        pub_locked = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub_locked, "--bs-primary: #118844")
        self.assertNotContains(pub_locked, "--bs-primary: #cc2255")

        # 3. Unlock -> public website follows admin app theme #cc2255 again
        self.client.force_login(self.staff)
        self.client.post(reverse("design_studio:website_theme_action"), {"website_theme_action": "unlock"})
        self.client.logout()
        pub_unlocked = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(pub_unlocked, "--bs-primary: #cc2255")

    def test_web_menu_brand_decoupling_logo_and_site_name(self):
        from site_kit.models import SiteConfig
        from site_kit.views import save_web_menu_config

        cfg = SiteConfig.get_solo()
        cfg.site_name = "Kunstverein Musterstadt"
        cfg.save()
        p = PublicPage.objects.create(title="BrandTest", is_published=True)

        # Hide site name in navbar, keep logo; <title> and footer still keep site_name
        save_web_menu_config({"show_logo": True, "show_site_name": False, "brand_title": "", "items": {}})
        res_no_name = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertNotContains(res_no_name, '<span class="brand-text">')
        self.assertContains(res_no_name, "bi-shield-check")
        self.assertContains(res_no_name, "<title>BrandTest – Kunstverein Musterstadt</title>")

        # Custom brand_title + hide logo
        save_web_menu_config({"show_logo": False, "show_site_name": True, "brand_title": "KV Kurz", "items": {}})
        res_custom = self.client.get(reverse("site_kit:page_detail", args=[p.slug]))
        self.assertContains(res_custom, '<span class="brand-text">KV Kurz</span>')
        self.assertNotContains(res_custom, "bi-shield-check")


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
