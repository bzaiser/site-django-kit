from functools import wraps
import json
from xml.sax.saxutils import escape

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import PageBlock, PublicPage, SiteConfig


SITE_EDITOR_GROUPS = ("Redakteur", "Leitung / Vorstand", "Vorstand")
SITE_CONFIG_GROUPS = ("Leitung / Vorstand", "Vorstand")


def _has_site_editor_access(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return user.groups.filter(name__in=SITE_EDITOR_GROUPS).exists()


def _has_site_config_access(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    return user.groups.filter(name__in=SITE_CONFIG_GROUPS).exists()


def _editor_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _has_site_editor_access(request.user):
            return HttpResponseForbidden("Redakteurs- oder Admin-Zugriff erforderlich.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _public_base_context(request, page: PublicPage | None = None) -> dict:
    cfg = SiteConfig.get_solo()
    nav_pages = list(PublicPage.objects.filter(is_published=True, show_in_nav=True))
    nav_keys = {p.slug.lower() for p in nav_pages if not p.is_home} | {
        p.title.strip().lower() for p in nav_pages if not p.is_home
    }
    can_access_internal = False
    if request.user.is_authenticated:
        if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False) or _has_site_editor_access(request.user):
            can_access_internal = True
        elif apps.is_installed("domain_kit"):
            try:
                from domain_kit.services import has_any_domain_access
                can_access_internal = has_any_domain_access(request.user)
            except Exception:
                can_access_internal = False

    ctx = {
        "site_cfg": cfg,
        "public_nav_pages": nav_pages,
        "show_default_news_nav": "news" not in nav_keys,
        "show_default_events_nav": "termine" not in nav_keys,
        "page": page,
        "can_access_internal": can_access_internal,
    }
    genre_key = (getattr(page, "genre", "") or cfg.default_genre or "").strip()
    if genre_key:
        try:
            from design_studio.genres import get_genre_definition
            g = get_genre_definition(genre_key)
            if g:
                ctx["active_genre"] = g
        except Exception:
            pass
    return ctx


def _format_bio_parts(bio_text: str) -> dict:
    intro = []
    bullets = []
    for raw_line in (bio_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("- ", "* ", "• ")):
            bullets.append(line[2:].strip())
        else:
            intro.append(line)
    return {"intro": intro, "bullets": bullets}


def _resolve_people_with_courses(people_qs):
    people = list(people_qs)
    now = timezone.now()
    courses_by_ref = {}
    if people and apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarEvent

            refs = [f"person:{p.id}" for p in people]
            for ev in CalendarEvent.objects.filter(is_public=True, instructor_ref__in=refs).order_by("-start_at"):
                courses_by_ref.setdefault(ev.instructor_ref, []).append(ev)
        except Exception:
            courses_by_ref = {}
    for p in people:
        p.bio_parts = _format_bio_parts(getattr(p, "bio", ""))
        evs = courses_by_ref.get(f"person:{p.id}", [])
        p.upcoming_courses = sorted([e for e in evs if e.end_at >= now], key=lambda e: e.start_at)[:6]
        p.past_courses = [e for e in evs if e.end_at < now][:8]
    return people


def _get_event_registration_counts() -> dict[str, int]:
    counts = {}
    if not apps.is_installed("list_kit"):
        return counts
    try:
        from list_kit.models import Entry

        for e in (
            Entry.objects.filter(collection__template__slug="kurs-anmeldung")
            .exclude(status__iexact="Abgelehnt")
            .only("custom_data_json")
        ):
            data = e.custom_data_json if isinstance(e.custom_data_json, dict) else {}
            eid = str(data.get("event_id") or "").strip()
            if eid:
                counts[eid] = counts.get(eid, 0) + 1
    except Exception:
        pass
    return counts


def _resolve_block_data(block: PageBlock) -> dict:
    cfg = block.config_json if isinstance(block.config_json, dict) else {}
    limit = int(cfg.get("limit") or 6)
    data = {"block": block, "config": cfg}

    if block.block_type == "news_feed" and apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_published_entries
            col_slug = (cfg.get("collection_slug") or "").strip() or None
            data["news_items"] = get_published_entries(content_type="news", collection_slug=col_slug, limit=limit)
        except Exception:
            data["news_items"] = []

    elif block.block_type == "faq_list" and apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_published_entries
            col_slug = (cfg.get("collection_slug") or "").strip() or None
            data["faq_items"] = get_published_entries(content_type="faq", collection_slug=col_slug, limit=limit)
        except Exception:
            data["faq_items"] = []

    elif block.block_type == "public_form" and apps.is_installed("list_kit"):
        try:
            from list_kit.models import Collection
            col_slug = (cfg.get("collection_slug") or "").strip()
            qs = Collection.objects.select_related("template").filter(is_public=True, allow_public_submit=True)
            col = qs.filter(slug=col_slug).first() if col_slug else qs.filter(template__content_type="form_inbox").first()
            data["form_collection"] = col
        except Exception:
            data["form_collection"] = None

    elif block.block_type == "upcoming_events" and apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarEvent
            now = timezone.now()
            data["events"] = list(
                CalendarEvent.objects.filter(is_public=True, end_at__gte=now).order_by("start_at")[:limit]
            )
        except Exception:
            data["events"] = []

    elif block.block_type == "people_grid" and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person

            qs = Person.objects.filter(is_public=True).prefetch_related("roles")
            role_filter = (cfg.get("role_filter") or "").strip()
            if role_filter:
                qs = qs.filter(roles__name__icontains=role_filter).distinct()
            data["people"] = _resolve_people_with_courses(qs[:limit])
        except Exception:
            data["people"] = []

    elif block.block_type == "downloads" and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Document
            data["documents"] = list(Document.objects.filter(is_public=True).order_by("-created_at")[:limit])
        except Exception:
            data["documents"] = []

    elif block.block_type == "stats_bar":
        raw_stats = cfg.get("stats") or []
        data["stats"] = raw_stats if isinstance(raw_stats, list) else []

    return data


def home_view(request):
    page = PublicPage.objects.filter(is_home=True, is_published=True).first()
    if page is None:
        page = PublicPage.objects.filter(is_published=True).first()
    ctx = _public_base_context(request, page=page)
    blocks = []
    if page is not None:
        for b in page.blocks.filter(is_visible=True):
            blocks.append(_resolve_block_data(b))
    ctx["blocks"] = blocks
    return render(request, "site_kit/page.html", ctx)


def page_detail_view(request, slug):
    qs = PublicPage.objects.all() if (request.user.is_authenticated and request.user.is_staff) else PublicPage.objects.filter(is_published=True)
    page = get_object_or_404(qs, slug=slug)
    ctx = _public_base_context(request, page=page)
    ctx["blocks"] = [_resolve_block_data(b) for b in page.blocks.filter(is_visible=True)]
    ctx["is_draft_preview"] = not page.is_published
    return render(request, "site_kit/page.html", ctx)


def news_list_view(request):
    ctx = _public_base_context(request)
    items = []
    if apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_published_entries
            items = get_published_entries(content_type="news", limit=30)
        except Exception:
            items = []
    ctx["news_items"] = items
    return render(request, "site_kit/news_list.html", ctx)


def news_detail_view(request, slug):
    if not apps.is_installed("list_kit"):
        raise Http404()
    from list_kit.models import Entry
    from list_kit.services import render_safe_markdown
    qs = Entry.objects.select_related("collection", "collection__template", "created_by").filter(
        slug=slug, collection__template__content_type="news"
    )
    if not (request.user.is_authenticated and request.user.is_staff):
        entry = get_object_or_404(qs, collection__is_public=True, visibility="public")
        if not entry.is_currently_published():
            raise Http404()
    else:
        entry = get_object_or_404(qs)
    ctx = _public_base_context(request)
    ctx["entry"] = entry
    ctx["rendered_body"] = render_safe_markdown(entry.notes)
    return render(request, "site_kit/news_detail.html", ctx)


def events_list_view(request):
    ctx = _public_base_context(request)
    events = []
    categories = []
    grouped_events = []
    active_calendar = (request.GET.get("kalender") or "").strip()

    if apps.is_installed("cal_kit"):
        try:
            from datetime import timedelta
            from cal_kit.models import CalendarCategory, CalendarEvent

            now = timezone.now()
            window_end = now + timedelta(days=180)
            public_qs = (
                CalendarEvent.objects.filter(is_public=True)
                .select_related("category")
                .order_by("start_at")
            )

            reg_counts = _get_event_registration_counts()

            def _ev_payload(ev, s_at, e_at):
                rc = reg_counts.get(str(ev.id), 0)
                mp = ev.max_participants
                return {
                    "event": ev,
                    "title": ev.title,
                    "start_at": s_at,
                    "end_at": e_at,
                    "all_day": ev.all_day,
                    "location": ev.location,
                    "description": ev.description,
                    "category": ev.category,
                    "instructor_name": ev.instructor_name,
                    "allow_registration": ev.allow_registration,
                    "max_participants": mp,
                    "reg_count": rc,
                    "is_full": bool(mp and rc >= mp),
                    "seats_left": max(0, mp - rc) if mp else None,
                }

            expanded = []
            for ev in public_qs:
                if ev.recurrence_rule == "none":
                    if ev.end_at >= now and ev.start_at <= window_end:
                        expanded.append(_ev_payload(ev, ev.start_at, ev.end_at))
                else:
                    for occ in ev.get_occurrences(now, window_end):
                        expanded.append(_ev_payload(ev, occ["start"], occ["end"]))

            expanded.sort(key=lambda item: item["start_at"])

            cat_counts = {}
            uncategorized_count = 0
            for item in expanded:
                cat = item["category"]
                if cat is None:
                    uncategorized_count += 1
                else:
                    if cat.pk not in cat_counts:
                        cat_counts[cat.pk] = {"obj": cat, "count": 0}
                    cat_counts[cat.pk]["count"] += 1

            state_colors = {
                1: "#2563eb",
                2: "#16a34a",
                3: "#d97706",
                4: "#dc2626",
                5: "#7c3aed",
                6: "#0891b2",
                7: "#475569",
            }
            for info in sorted(
                cat_counts.values(),
                key=lambda x: x["obj"].name.lower(),
            ):
                cat = info["obj"]
                categories.append({
                    "key": str(cat.pk),
                    "name": cat.name,
                    "color": state_colors.get(cat.color_state, "#2563eb"),
                    "icon": cat.icon or "bi-calendar-event",
                    "count": info["count"],
                })
            if uncategorized_count:
                categories.append({
                    "key": "allgemein",
                    "name": "Allgemein",
                    "color": "#64748b",
                    "icon": "bi-calendar-event",
                    "count": uncategorized_count,
                })

            if active_calendar == "allgemein":
                filtered = [item for item in expanded if item["category"] is None]
            elif active_calendar.isdigit():
                cat_id = int(active_calendar)
                filtered = [
                    item for item in expanded
                    if item["category"] is not None and item["category"].pk == cat_id
                ]
            else:
                active_calendar = ""
                filtered = expanded

            events = filtered[:80]

            groups_map = {}
            for item in events:
                cat = item["category"]
                key = str(cat.pk) if cat is not None else "allgemein"
                if key not in groups_map:
                    groups_map[key] = {
                        "key": key,
                        "name": cat.name if cat is not None else "Allgemein",
                        "color": state_colors.get(cat.color_state, "#2563eb") if cat is not None else "#64748b",
                        "icon": (cat.icon or "bi-calendar-event") if cat is not None else "bi-calendar-event",
                        "items": [],
                    }
                groups_map[key]["items"].append(item)
            grouped_events = list(groups_map.values())
        except Exception:
            events = []

    ctx.update({
        "events": events,
        "categories": categories,
        "grouped_events": grouped_events,
        "active_calendar": active_calendar,
        "total_public_events": sum(c["count"] for c in categories),
    })
    return render(request, "site_kit/events_list.html", ctx)


def lecturers_list_view(request):
    ctx = _public_base_context(request)
    lecturers = []
    if apps.is_installed("domain_kit"):
        try:
            from django.db.models import Q
            from domain_kit.models import Person

            qs = (
                Person.objects.filter(is_public=True)
                .filter(
                    Q(roles__name__icontains="dozent")
                    | Q(roles__name__icontains="referent")
                    | Q(roles__name__icontains="kursleiter")
                    | ~Q(bio="")
                )
                .distinct()
                .prefetch_related("roles")
            )
            lecturers = _resolve_people_with_courses(qs)
        except Exception:
            lecturers = []
    ctx["lecturers"] = lecturers
    return render(request, "site_kit/lecturers_list.html", ctx)


def event_registration_view(request):
    ctx = _public_base_context(request)
    event_obj = None
    reg_count = 0
    is_full = False
    form_collection = None

    ev_id = (request.GET.get("event") or "").strip()
    if ev_id.isdigit() and apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarEvent

            event_obj = CalendarEvent.objects.filter(pk=int(ev_id), is_public=True).first()
            if event_obj:
                reg_count = _get_event_registration_counts().get(str(event_obj.id), 0)
                if event_obj.max_participants and reg_count >= event_obj.max_participants:
                    is_full = True
        except Exception:
            event_obj = None

    if apps.is_installed("list_kit"):
        try:
            from list_kit.models import Collection

            qs = Collection.objects.select_related("template").filter(is_public=True, allow_public_submit=True)
            form_collection = qs.filter(template__slug="kurs-anmeldung").first() or qs.filter(template__content_type="form_inbox").first()
        except Exception:
            form_collection = None

    ctx.update({
        "event_obj": event_obj,
        "reg_count": reg_count,
        "is_full": is_full,
        "form_collection": form_collection,
    })
    return render(request, "site_kit/event_registration.html", ctx)


def imprint_view(request):
    ctx = _public_base_context(request)
    ctx["legal_title"] = "Impressum"
    ctx["legal_body"] = ctx["site_cfg"].imprint_text or "Angaben gemäß § 5 DDG folgen."
    return render(request, "site_kit/legal.html", ctx)


def privacy_view(request):
    ctx = _public_base_context(request)
    ctx["legal_title"] = "Datenschutzerklärung"
    ctx["legal_body"] = ctx["site_cfg"].privacy_text or "Hinweise zum Datenschutz folgen."
    return render(request, "site_kit/legal.html", ctx)


def sitemap_view(request):
    base = request.build_absolute_uri("/").rstrip("/")
    urls = [f"{base}/", f"{base}/news/", f"{base}/termine/", f"{base}/impressum/", f"{base}/datenschutz/"]
    for p in PublicPage.objects.filter(is_published=True, is_home=False):
        urls.append(f"{base}/seite/{p.slug}/")
    if apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_published_entries
            for e in get_published_entries(content_type="news", limit=100):
                urls.append(f"{base}/news/{e.slug}/")
        except Exception:
            pass
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        xml.append(f"<url><loc>{escape(u)}</loc></url>")
    xml.append("</urlset>")
    return HttpResponse("\n".join(xml), content_type="application/xml; charset=utf-8")


def robots_view(request):
    base = request.build_absolute_uri("/").rstrip("/")
    lines = ["User-agent: *", "Allow: /", f"Sitemap: {base}/sitemap.xml"]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


def news_rss_view(request):
    cfg = SiteConfig.get_solo()
    base = request.build_absolute_uri("/").rstrip("/")
    items = []
    if apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_published_entries
            items = get_published_entries(content_type="news", limit=20)
        except Exception:
            items = []
    xml = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{escape(cfg.site_name)} – News</title>",
        f"<link>{escape(base)}/news/</link>",
        f"<description>{escape(cfg.tagline or cfg.site_name)}</description>",
    ]
    for e in items:
        link = f"{base}/news/{e.slug}/"
        xml.append(f"<item><title>{escape(e.title)}</title><link>{escape(link)}</link><description>{escape(e.notes[:300])}</description></item>")
    xml.append("</channel></rss>")
    return HttpResponse("\n".join(xml), content_type="application/rss+xml; charset=utf-8")


@_editor_required
def manage_view(request):
    cfg = SiteConfig.get_solo()
    can_edit_config = _has_site_config_access(request.user)
    if request.method == "POST":
        if not can_edit_config:
            return HttpResponseForbidden("Nur Leitung oder Administrator darf globale Webseiten-Einstellungen ändern.")
        cfg.site_name = (request.POST.get("site_name") or cfg.site_name).strip()[:140]
        cfg.tagline = (request.POST.get("tagline") or "").strip()[:220]
        cfg.contact_email = (request.POST.get("contact_email") or "").strip()
        cfg.footer_text = (request.POST.get("footer_text") or "").strip()[:220]
        cfg.imprint_text = (request.POST.get("imprint_text") or "").strip()
        cfg.privacy_text = (request.POST.get("privacy_text") or "").strip()
        cfg.default_genre = (request.POST.get("default_genre") or "").strip()[:40]
        cfg.save()
        messages.success(request, "Webseiten-Einstellungen gespeichert.")
        return redirect("site_kit:manage")

    pages = PublicPage.objects.all()
    return render(
        request,
        "site_kit/manage.html",
        {"site_cfg": cfg, "pages": pages, "can_edit_site_config": can_edit_config},
    )


def _warn_if_reserved_nav_page(request, page: PublicPage):
    if page.is_published and page.show_in_nav and not page.is_home:
        slug_l = page.slug.lower()
        title_l = page.title.strip().lower()
        if slug_l in ("news", "termine") or title_l in ("news", "termine"):
            target = "/news/" if ("news" in (slug_l, title_l)) else "/termine/"
            messages.warning(
                request,
                f"Hinweis: Es existiert bereits ein Standard-Link für „{page.title}“ ({target}). Der Standard-Link wird in der Navigation ausgeblendet, solange diese Seite im Menü aktiv ist.",
            )


@_editor_required
def page_create_view(request):
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()
        if title:
            p = PublicPage.objects.create(
                title=title[:160],
                is_home=request.POST.get("is_home") == "on",
                is_published=request.POST.get("is_published") == "on",
                show_in_nav=request.POST.get("show_in_nav") == "on",
            )
            messages.success(request, f"Seite „{p.title}“ erstellt.")
            _warn_if_reserved_nav_page(request, p)
            return redirect("site_kit:page_edit", pk=p.id)
    return redirect("site_kit:manage")


@_editor_required
def page_delete_view(request, pk):
    page = get_object_or_404(PublicPage, pk=pk)
    if request.method == "POST":
        title = page.title
        page.delete()
        messages.success(request, f"Seite „{title}“ gelöscht.")
    return redirect("site_kit:manage")


@_editor_required
def page_edit_view(request, pk):
    page = get_object_or_404(PublicPage, pk=pk)
    if request.method == "POST":
        action = request.POST.get("action") or "save_page"
        if action == "add_block":
            btype = request.POST.get("block_type") or "text_image"
            if btype in dict(PageBlock.BLOCK_TYPE_CHOICES):
                pos = (page.blocks.count() + 1) * 10
                PageBlock.objects.create(
                    page=page,
                    block_type=btype,
                    title=(request.POST.get("title") or "").strip()[:180],
                    subtitle=(request.POST.get("subtitle") or "").strip()[:255],
                    body=(request.POST.get("body") or "").strip(),
                    image=request.FILES.get("image"),
                    image_url=(request.POST.get("image_url") or "").strip()[:200],
                    cta_label=(request.POST.get("cta_label") or "").strip()[:80],
                    cta_url=(request.POST.get("cta_url") or "").strip()[:255],
                    position=pos,
                )
                messages.success(request, "Inhaltsblock hinzugefügt.")
        elif action == "delete_block":
            bid = request.POST.get("block_id")
            if bid:
                PageBlock.objects.filter(pk=bid, page=page).delete()
                messages.success(request, "Block entfernt.")
        elif action == "reorder_blocks":
            order_raw = (request.POST.get("order_json") or "").strip()
            try:
                ids = json.loads(order_raw)
                for idx, bid in enumerate(ids):
                    PageBlock.objects.filter(pk=bid, page=page).update(position=(idx + 1) * 10)
                messages.success(request, "Reihenfolge gespeichert.")
            except Exception:
                pass
        else:
            page.title = (request.POST.get("title") or page.title).strip()[:160]
            page.is_home = request.POST.get("is_home") == "on"
            page.is_published = request.POST.get("is_published") == "on"
            page.show_in_nav = request.POST.get("show_in_nav") == "on"
            page.genre = (request.POST.get("genre") or "").strip()[:40]
            page.meta_description = (request.POST.get("meta_description") or "").strip()[:255]
            page.save()
            messages.success(request, "Seiteneinstellungen gespeichert.")
            _warn_if_reserved_nav_page(request, page)
        return redirect("site_kit:page_edit", pk=page.id)

    return render(request, "site_kit/page_editor.html", {
        "page": page,
        "blocks": page.blocks.all(),
        "block_choices": PageBlock.BLOCK_TYPE_CHOICES,
    })
