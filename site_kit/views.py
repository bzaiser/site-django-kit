from functools import wraps
from html.parser import HTMLParser
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

    show_default_program_nav = False
    program_nav_label = "Programm"
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import OfferingTemplate
            from cal_kit.services import get_cal_profile_labels
            if "programm" not in nav_keys and "jahresprogramm" not in nav_keys and OfferingTemplate.objects.filter(show_in_program=True).exists():
                show_default_program_nav = True
            program_nav_label = get_cal_profile_labels().get("program_title") or "Programm"
        except Exception:
            pass

    ctx = {
        "site_cfg": cfg,
        "public_nav_pages": nav_pages,
        "show_default_news_nav": "news" not in nav_keys,
        "show_default_events_nav": "termine" not in nav_keys,
        "show_default_program_nav": show_default_program_nav,
        "program_nav_label": program_nav_label,
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


def _parse_video_embed(raw_url: str) -> tuple[str, str]:
    """Return (provider_label, embed_url) for YouTube/Vimeo links, or ('', '') for direct video files."""
    import re
    u = (raw_url or "").strip()
    if not u:
        return ("", "")
    yt = re.search(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,20})", u)
    if yt:
        vid = yt.group(1)
        return ("YouTube", f"https://www.youtube-nocookie.com/embed/{vid}?autoplay=1")
    vm = re.search(r"vimeo\.com/(?:video/)?(\d+)", u)
    if vm:
        vid = vm.group(1)
        return ("Vimeo", f"https://player.vimeo.com/video/{vid}?autoplay=1")
    return ("", "")


def _attach_news_preview_html(entries):
    from django.utils.html import escape, linebreaks
    for e in entries:
        raw = (getattr(e, "notes", "") or "").strip()
        if not raw:
            e.preview_html = ""
        elif "<" in raw and ">" in raw:
            e.preview_html = _sanitize_rich_html(raw)
        else:
            e.preview_html = linebreaks(escape(raw))
    return entries


def _resolve_block_data(block: PageBlock) -> dict:
    cfg = block.config_json if isinstance(block.config_json, dict) else {}
    limit = int(cfg.get("limit") or 6)
    data = {"block": block, "config": cfg}

    if block.block_type == "news_feed" and apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_published_entries
            col_slug = (cfg.get("collection_slug") or "").strip() or None
            data["news_items"] = _attach_news_preview_html(
                get_published_entries(content_type="news", collection_slug=col_slug, limit=limit)
            )
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
            qs = CalendarEvent.objects.filter(is_public=True, end_at__gte=now)
            cal_cat = (cfg.get("calendar_category") or "").strip()
            if cal_cat.isdigit():
                qs = qs.filter(category_id=int(cal_cat))
                data["calendar_category_id"] = cal_cat
            data["events"] = list(qs.order_by("start_at")[: max(1, min(50, limit))])
        except Exception:
            data["events"] = []

    elif block.block_type == "people_grid" and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person

            qs = Person.objects.filter(is_public=True).prefetch_related("roles")
            role_filter = (cfg.get("role_filter") or "").strip()
            if role_filter:
                qs = qs.filter(roles__name__icontains=role_filter).distinct()
            cat_filter = (cfg.get("category_filter") or "").strip()
            if cat_filter:
                qs = qs.filter(category__name__icontains=cat_filter)
            data["people"] = _resolve_people_with_courses(qs[:limit])
        except Exception:
            data["people"] = []
        data["show_photos"] = cfg.get("show_photos", True)
        data["person_col_span"] = int(cfg.get("person_col_span") or 4)
        data["person_card_style"] = cfg.get("person_card_style") or "card"

    elif block.block_type == "downloads" and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Document
            data["documents"] = list(Document.objects.filter(is_public=True).order_by("-created_at")[:limit])
        except Exception:
            data["documents"] = []

    elif block.block_type == "annual_program":
        prog = _build_program_data(
            year=cfg.get("program_year"),
            cat_id=cfg.get("calendar_category"),
        )
        data["program_items"] = prog["items"]
        data["program_year"] = prog["year"]
        data["program_title"] = prog["program_title"]

    elif block.block_type == "photo_gallery":
        col_slug = (cfg.get("collection_slug") or "").strip() or None
        photos = []
        if apps.is_installed("list_kit"):
            try:
                from list_kit.services import get_gallery_photos
                photos = get_gallery_photos(collection_slug=col_slug, limit=int(cfg.get("limit") or 60))
            except Exception:
                photos = []
        linked_event = None
        ev_id = str(cfg.get("linked_event_id") or "").strip()
        if ev_id.isdigit() and apps.is_installed("cal_kit"):
            try:
                from cal_kit.models import CalendarEvent
                linked_event = CalendarEvent.objects.filter(pk=int(ev_id), is_public=True).first()
            except Exception:
                linked_event = None
        linked_person = None
        pid = str(cfg.get("linked_person_id") or "").strip()
        if pid.isdigit() and apps.is_installed("domain_kit"):
            try:
                from domain_kit.models import Person
                linked_person = Person.objects.filter(pk=int(pid), is_public=True).first()
            except Exception:
                linked_person = None
        data["gallery_photos"] = photos
        data["gallery_layout"] = cfg.get("gallery_layout") if cfg.get("gallery_layout") in ("grid", "carousel") else "grid"
        data["gallery_cols"] = int(cfg.get("gallery_cols") or 3)
        raw_iv = cfg.get("gallery_interval")
        data["gallery_interval"] = int(raw_iv) if str(raw_iv).isdigit() and int(raw_iv) in (0, 3000, 5000, 8000) else 5000
        cm = cfg.get("gallery_caption_mode")
        data["gallery_caption_mode"] = cm if cm in ("below", "overlay", "none") else "below"
        data["gallery_show_thumbs"] = cfg.get("gallery_show_thumbs", True) is not False and str(cfg.get("gallery_show_thumbs", "1")) != "0"
        raw_gh = cfg.get("gallery_height")
        data["gallery_height"] = int(raw_gh) if str(raw_gh).isdigit() and int(raw_gh) in (300, 440, 580) else 440
        data["linked_event"] = linked_event
        data["linked_person"] = linked_person

    elif block.block_type == "video":
        v_url = block.resolved_video_url
        provider, embed_url = _parse_video_embed(v_url)
        data["video_src"] = v_url
        data["video_provider"] = provider
        data["video_embed_url"] = embed_url
        data["video_is_file"] = bool(block.video_file) or (bool(v_url) and not embed_url)

    elif block.block_type == "stats_bar":
        raw_stats = []
        for line in (block.body or "").splitlines():
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                val_part = parts[0] if len(parts) >= 1 else ""
                lbl_part = parts[1] if len(parts) >= 2 else ""
                icon_part = parts[2][:40] if len(parts) >= 3 else ""
                mode_raw = parts[3].upper() if len(parts) >= 4 and parts[3].strip() else ("F" if icon_part else "")
                if mode_raw == "BF":
                    mode_raw = "FB"
                if mode_raw not in ("F", "B", "FB"):
                    mode_raw = "F" if icon_part else ""
                if val_part and lbl_part:
                    raw_stats.append({
                        "value": val_part[:40],
                        "label": lbl_part[:80],
                        "icon": icon_part,
                        "is_bi": icon_part.startswith("bi-"),
                        "icon_mode": mode_raw,
                    })
        if not raw_stats and isinstance(cfg.get("stats"), list):
            raw_stats = cfg.get("stats") or []
        if not raw_stats:
            try:
                if apps.is_installed("cal_kit"):
                    from cal_kit.models import CalendarEvent
                    ev_cnt = CalendarEvent.objects.filter(is_public=True, end_at__gte=timezone.now()).count()
                    raw_stats.append({"value": ev_cnt, "label": "Öffentliche Termine"})
                if apps.is_installed("list_kit"):
                    from list_kit.services import get_published_entries
                    news_cnt = len(get_published_entries(content_type="news", limit=100))
                    raw_stats.append({"value": news_cnt, "label": "Aktuelle News"})
                if apps.is_installed("domain_kit"):
                    from domain_kit.models import Document, Person
                    raw_stats.append({"value": Person.objects.filter(is_public=True).count(), "label": "Ansprechpartner"})
                    raw_stats.append({"value": Document.objects.filter(is_public=True).count(), "label": "Downloads"})
            except Exception:
                pass
        data["stats"] = raw_stats

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
    if slug == "termine":
        return events_list_view(request)
    if slug == "news":
        return news_list_view(request)
    qs = PublicPage.objects.all() if (request.user.is_authenticated and request.user.is_staff) else PublicPage.objects.filter(is_published=True)
    page = get_object_or_404(qs, slug=slug)
    ctx = _public_base_context(request, page=page)
    ctx["blocks"] = [_resolve_block_data(b) for b in page.blocks.filter(is_visible=True)]
    ctx["is_draft_preview"] = not page.is_published
    return render(request, "site_kit/page.html", ctx)


def news_list_view(request):
    from django.core.paginator import Paginator
    sys_page = PublicPage.objects.filter(slug="news").first()
    ctx = _public_base_context(request, page=sys_page)
    ctx["extra_blocks"] = [_resolve_block_data(b) for b in sys_page.blocks.filter(is_visible=True)] if sys_page else []
    items = []
    archive_count = 0
    if apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_archived_entries, get_published_entries
            items = _attach_news_preview_html(get_published_entries(content_type="news"))
            archive_count = len(get_archived_entries(content_type="news"))
        except Exception:
            items = []
    page_obj = Paginator(items, 18).get_page(request.GET.get("page"))
    ctx["news_items"] = page_obj
    ctx["page_obj"] = page_obj
    ctx["archive_count"] = archive_count
    return render(request, "site_kit/news_list.html", ctx)


def news_archive_view(request):
    from django.core.paginator import Paginator
    ctx = _public_base_context(request)
    raw_year = (request.GET.get("jahr") or "").strip()
    sel_year = int(raw_year) if raw_year.isdigit() else None
    items = []
    years = []
    if apps.is_installed("list_kit"):
        try:
            from list_kit.services import get_archived_entries
            all_archived = get_archived_entries(content_type="news")
            years = sorted({(e.publish_at or e.created_at).year for e in all_archived}, reverse=True)
            items = _attach_news_preview_html(
                [e for e in all_archived if not sel_year or (e.publish_at or e.created_at).year == sel_year]
            )
        except Exception:
            items = []
    page_obj = Paginator(items, 20).get_page(request.GET.get("page"))
    ctx.update({
        "news_items": page_obj,
        "page_obj": page_obj,
        "archive_years": years,
        "active_year": sel_year,
    })
    return render(request, "site_kit/news_archive.html", ctx)


def news_detail_view(request, slug):
    if not apps.is_installed("list_kit"):
        raise Http404()
    from list_kit.models import Entry
    from list_kit.services import get_linked_domain_document, render_safe_markdown
    qs = Entry.objects.select_related("collection", "collection__template", "created_by").filter(
        slug=slug, collection__template__content_type="news"
    )
    now = timezone.now()
    if not (request.user.is_authenticated and request.user.is_staff):
        entry = get_object_or_404(qs, collection__is_public=True, visibility="public")
        if entry.publish_at and entry.publish_at > now:
            raise Http404()
    else:
        entry = get_object_or_404(qs)
    ctx = _public_base_context(request)
    ctx["entry"] = entry
    ctx["is_archived"] = bool(entry.expires_at and entry.expires_at <= now)
    raw_body = (entry.notes or "").strip()
    if entry.collection.template.use_rich_notes or ("<" in raw_body and ">" in raw_body):
        ctx["rendered_body"] = _sanitize_rich_html(raw_body)
    else:
        ctx["rendered_body"] = render_safe_markdown(raw_body)
    ctx["public_documents"] = list(entry.documents.filter(is_public=True))
    ctx["linked_document"] = get_linked_domain_document(entry.linked_document_id, require_public=True) if entry.linked_document_id else None
    return render(request, "site_kit/news_detail.html", ctx)


def events_list_view(request):
    sys_page = PublicPage.objects.filter(slug="termine").first()
    ctx = _public_base_context(request, page=sys_page)
    ctx["extra_blocks"] = [_resolve_block_data(b) for b in sys_page.blocks.filter(is_visible=True)] if sys_page else []
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
                .select_related("category", "offering_template")
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
                    "image_url": getattr(ev, "resolved_image_url", ""),
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
                    for occ_start, occ_end, _occ_title in ev.get_occurrences(now, window_end):
                        expanded.append(_ev_payload(ev, occ_start, occ_end))

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
            import logging
            logging.getLogger(__name__).exception("events_list_view failed")
            events = []

    ctx.update({
        "events": events,
        "categories": categories,
        "grouped_events": grouped_events,
        "active_calendar": active_calendar,
        "total_public_events": sum(c["count"] for c in categories),
    })
    return render(request, "site_kit/events_list.html", ctx)


def event_detail_view(request, pk):
    if not apps.is_installed("cal_kit"):
        raise Http404()
    from datetime import timedelta
    from cal_kit.models import CalendarEvent

    qs = CalendarEvent.objects.select_related("category", "offering_template")
    if not (request.user.is_authenticated and request.user.is_staff):
        ev = get_object_or_404(qs, pk=pk, is_public=True)
    else:
        ev = get_object_or_404(qs, pk=pk)

    ctx = _public_base_context(request)
    reg_counts = _get_event_registration_counts()
    rc = reg_counts.get(str(ev.id), 0)
    mp = ev.max_participants
    tpl = ev.offering_template

    raw_ev_desc = (ev.description or "").strip()
    if raw_ev_desc:
        if "<" in raw_ev_desc and ">" in raw_ev_desc:
            ev_desc_html = _sanitize_rich_html(raw_ev_desc)
        else:
            from django.utils.html import escape, linebreaks
            ev_desc_html = linebreaks(escape(raw_ev_desc))
    else:
        ev_desc_html = ""

    tpl_desc_html = ""
    if tpl and (tpl.description or "").strip():
        raw_tpl_desc = tpl.description.strip()
        if raw_tpl_desc != raw_ev_desc:
            if "<" in raw_tpl_desc and ">" in raw_tpl_desc:
                tpl_desc_html = _sanitize_rich_html(raw_tpl_desc)
            else:
                from django.utils.html import escape, linebreaks
                tpl_desc_html = linebreaks(escape(raw_tpl_desc))

    instructor_person_id = None
    if (ev.instructor_ref or "").startswith("person:"):
        raw_pid = ev.instructor_ref.split(":", 1)[1].strip()
        if raw_pid.isdigit():
            instructor_person_id = int(raw_pid)

    upcoming_occurrences = []
    if ev.recurrence_rule != "none":
        now = timezone.now()
        for occ_s, occ_e, _t in ev.get_occurrences(now, now + timedelta(days=180)):
            upcoming_occurrences.append({"start_at": occ_s, "end_at": occ_e})
            if len(upcoming_occurrences) >= 8:
                break

    ctx.update({
        "ev": ev,
        "offering": tpl,
        "image_url": getattr(ev, "resolved_image_url", ""),
        "event_desc_html": ev_desc_html,
        "offering_desc_html": tpl_desc_html,
        "fee_amount": ev.fee_amount if ev.fee_amount is not None else (tpl.default_fee if tpl else None),
        "instructor_person_id": instructor_person_id,
        "reg_count": rc,
        "max_participants": mp,
        "is_full": bool(mp and rc >= mp),
        "seats_left": max(0, mp - rc) if mp else None,
        "upcoming_occurrences": upcoming_occurrences,
    })
    return render(request, "site_kit/event_detail.html", ctx)


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


def person_detail_view(request, pk):
    ctx = _public_base_context(request)
    if not apps.is_installed("domain_kit"):
        raise Http404("Personenmodul nicht aktiviert.")
    from domain_kit.models import Document, Person

    can_preview = request.user.is_authenticated and (
        getattr(request.user, "is_staff", False)
        or getattr(request.user, "is_superuser", False)
        or _has_site_editor_access(request.user)
    )
    qs = Person.objects.prefetch_related("roles")
    if not can_preview:
        qs = qs.filter(is_public=True)
    person_obj = get_object_or_404(qs, pk=pk)
    resolved = _resolve_people_with_courses([person_obj])
    person = resolved[0]
    docs = list(Document.objects.filter(person=person_obj, is_public=True).order_by("-document_date", "-created_at")[:12])
    ctx.update({"person": person, "person_docs": docs})
    return render(request, "site_kit/person_detail.html", ctx)


def _build_program_data(year=None, cat_id=None, instructor_ref=None) -> dict:
    items = []
    categories = []
    instructors = []
    years = []
    program_title = "Jahresprogramm"
    now = timezone.now()
    sel_year = int(year) if year and str(year).isdigit() else None

    if not apps.is_installed("cal_kit"):
        return {
            "items": items,
            "categories": categories,
            "instructors": instructors,
            "years": years,
            "year": sel_year or now.year,
            "program_title": program_title,
        }

    try:
        from cal_kit.models import CalendarCategory, CalendarEvent, OfferingTemplate
        from cal_kit.services import get_cal_profile_labels

        program_title = get_cal_profile_labels().get("program_title") or "Jahresprogramm"
        categories = list(CalendarCategory.objects.filter(is_public_default=True).order_by("name").values("id", "name"))
        if not categories:
            categories = list(CalendarCategory.objects.filter(is_shared=True).order_by("name").values("id", "name"))

        people_map = {}
        if apps.is_installed("domain_kit"):
            from domain_kit.models import Person
            for p in Person.objects.all():
                people_map[f"person:{p.id}"] = p

        reg_counts = {}
        if apps.is_installed("list_kit"):
            from list_kit.models import Entry
            for ev_id in (
                Entry.objects.filter(collection__template__slug="kurs-anmeldung")
                .exclude(status__iexact="Abgelehnt")
                .exclude(status__iexact="Abgesagt")
                .values_list("custom_data_json__event_id", flat=True)
            ):
                if ev_id:
                    reg_counts[str(ev_id)] = reg_counts.get(str(ev_id), 0) + 1

        ev_years = set(CalendarEvent.objects.filter(is_public=True).dates("start_at", "year"))
        years = sorted({d.year for d in ev_years} | {now.year}, reverse=True)
        if not sel_year:
            sel_year = now.year

        qs = OfferingTemplate.objects.filter(show_in_program=True).select_related("category").order_by("title")
        if cat_id and str(cat_id).isdigit():
            qs = qs.filter(category_id=int(cat_id))
        if instructor_ref:
            qs = qs.filter(instructor_ref=instructor_ref)

        seen_instructors = {}
        for tpl in OfferingTemplate.objects.filter(show_in_program=True).order_by("instructor_name"):
            if tpl.instructor_ref and tpl.instructor_name and tpl.instructor_ref not in seen_instructors:
                seen_instructors[tpl.instructor_ref] = tpl.instructor_name
        instructors = [{"ref": k, "name": v} for k, v in seen_instructors.items()]

        for tpl in qs:
            ev_qs = tpl.events.filter(is_public=True).order_by("start_at")
            if sel_year:
                ev_qs = ev_qs.filter(start_at__year=sel_year)
            ev_rows = []
            for ev in ev_qs:
                cnt = reg_counts.get(str(ev.id), 0)
                is_past = ev.end_at < now
                is_full = bool(ev.max_participants and cnt >= ev.max_participants)
                ev_rows.append({
                    "event": ev,
                    "reg_count": cnt,
                    "is_past": is_past,
                    "is_full": is_full,
                    "can_register": bool(ev.allow_registration and not is_past and not is_full),
                })

            inst_person = people_map.get(tpl.instructor_ref)
            resp_person = people_map.get(tpl.responsible_ref)
            raw_d = (tpl.description or "").strip()
            if raw_d:
                if "<" in raw_d and ">" in raw_d:
                    d_html = _sanitize_rich_html(raw_d)
                else:
                    from django.utils.html import escape, linebreaks
                    d_html = linebreaks(escape(raw_d))
            else:
                d_html = ""
            items.append({
                "offering": tpl,
                "description_html": d_html,
                "events": ev_rows,
                "instructor_person": inst_person if (inst_person and inst_person.is_public) else None,
                "responsible_person": resp_person,
            })
    except Exception:
        pass

    return {
        "items": items,
        "categories": categories,
        "instructors": instructors,
        "years": years,
        "year": sel_year or now.year,
        "program_title": program_title,
    }


def annual_program_view(request):
    ctx = _public_base_context(request)
    year = (request.GET.get("jahr") or "").strip()
    cat_id = (request.GET.get("kategorie") or "").strip()
    inst_ref = (request.GET.get("dozent") or "").strip()
    prog = _build_program_data(year=year or None, cat_id=cat_id or None, instructor_ref=inst_ref or None)
    ctx.update({
        "program_items": prog["items"],
        "program_categories": prog["categories"],
        "program_instructors": prog["instructors"],
        "program_years": prog["years"],
        "active_year": prog["year"],
        "active_category": cat_id,
        "active_instructor": inst_ref,
        "program_title": prog["program_title"],
    })
    return render(request, "site_kit/annual_program.html", ctx)


def annual_program_pdf_view(request):
    from io import BytesIO
    from django.template.loader import get_template

    ctx = _public_base_context(request)
    year = (request.GET.get("jahr") or "").strip()
    cat_id = (request.GET.get("kategorie") or "").strip()
    inst_ref = (request.GET.get("dozent") or "").strip()
    prog = _build_program_data(year=year or None, cat_id=cat_id or None, instructor_ref=inst_ref or None)
    ctx.update({
        "program_items": prog["items"],
        "active_year": prog["year"],
        "program_title": prog["program_title"],
    })
    html = get_template("site_kit/annual_program_pdf.html").render(ctx)
    try:
        from xhtml2pdf import pisa
        result = BytesIO()
        pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result)
        if not pdf.err:
            resp = HttpResponse(result.getvalue(), content_type="application/pdf")
            resp["Content-Disposition"] = f'inline; filename="programm-{prog["year"]}.pdf"'
            return resp
    except ImportError:
        pass
    return HttpResponse(html)


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


_ALLOWED_HTML_TAGS = {
    "p", "br", "hr", "h2", "h3", "h4", "h5", "strong", "b", "em", "i", "u",
    "ul", "ol", "li", "blockquote", "a", "img", "div", "span", "font", "table", "thead",
    "tbody", "tr", "th", "td",
}
_VOID_HTML_TAGS = {"br", "hr", "img"}
_STRIP_WITH_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "form"}
_ALLOWED_STYLE_PROPS = {
    "color", "background-color", "text-align", "font-weight", "font-style",
    "text-decoration", "max-width", "width", "height", "border-radius",
}


def _sanitize_inline_style(raw_style: str) -> str:
    safe_parts = []
    for decl in (raw_style or "").split(";"):
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop_l = prop.strip().lower()
        val_s = val.strip()
        if prop_l not in _ALLOWED_STYLE_PROPS or not val_s:
            continue
        val_l = val_s.lower()
        if any(bad in val_l for bad in ("url(", "expression(", "javascript:", "@import")):
            continue
        safe_parts.append(f"{prop_l}: {val_s}")
    return "; ".join(safe_parts)


class _SafeHtmlSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in _STRIP_WITH_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0 or t not in _ALLOWED_HTML_TAGS:
            return
        clean_attrs = []
        for k, v in attrs:
            kl = (k or "").lower()
            if kl == "class" and v:
                clean_attrs.append(f'class="{escape(v)}"')
            elif kl == "style" and v:
                safe_style = _sanitize_inline_style(v)
                if safe_style:
                    clean_attrs.append(f'style="{escape(safe_style)}"')
            elif t == "font" and kl == "color" and v:
                safe_col = _sanitize_inline_style(f"color: {v}")
                if safe_col:
                    clean_attrs.append(f'style="{escape(safe_col)}"')
            elif t == "a" and kl == "href" and v:
                vl = v.strip()
                if vl.startswith(("http://", "https://", "/", "#")):
                    clean_attrs.append(f'href="{escape(vl)}"')
            elif t == "a" and kl == "target" and v in ("_blank", "_self"):
                clean_attrs.append(f'target="{v}"')
            elif t == "img" and kl == "src" and v:
                vl = v.strip()
                if vl.startswith(("http://", "https://", "/", "data:image/")):
                    clean_attrs.append(f'src="{escape(vl)}"')
            elif t == "img" and kl == "alt" and v is not None:
                clean_attrs.append(f'alt="{escape(v)}"')
        attr_str = (" " + " ".join(clean_attrs)) if clean_attrs else ""
        self.out.append(f"<{t}{attr_str}>")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in _STRIP_WITH_CONTENT_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        if self._skip_depth > 0 or t not in _ALLOWED_HTML_TAGS or t in _VOID_HTML_TAGS:
            return
        self.out.append(f"</{t}>")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.out.append(escape(data))

    def handle_entityref(self, name):
        if self._skip_depth == 0:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if self._skip_depth == 0:
            self.out.append(f"&#{name};")


def _sanitize_rich_html(raw_html: str) -> str:
    parser = _SafeHtmlSanitizer()
    parser.feed(raw_html or "")
    parser.close()
    return "".join(parser.out).strip()


def _parse_col_span(raw_val) -> int:
    try:
        val = int(raw_val)
        return val if val in (12, 8, 6, 4, 3) else 12
    except (TypeError, ValueError):
        return 12


def _parse_block_layout_config(request, existing_cfg=None) -> dict:
    cfg = dict(existing_cfg) if isinstance(existing_cfg, dict) else {}
    sv = (request.POST.get("style_variant") or cfg.get("style_variant") or "card").strip()
    cfg["style_variant"] = sv if sv in ("card", "solid", "flat", "subtle", "primary") else "card"
    ip = (request.POST.get("image_position") or cfg.get("image_position") or "right").strip()
    cfg["image_position"] = ip if ip in ("right", "left", "top") else "right"
    ishape = (request.POST.get("image_shape") or cfg.get("image_shape") or "rounded").strip()
    cfg["image_shape"] = ishape if ishape in ("rounded", "square", "circle") else "rounded"
    iasp = (request.POST.get("image_aspect") or cfg.get("image_aspect") or "auto").strip()
    cfg["image_aspect"] = iasp if iasp in ("auto", "landscape", "portrait", "square") else "auto"
    if "compact" in request.POST or "layout_fields_present" in request.POST:
        cfg["compact"] = request.POST.get("compact") == "on"
    if "show_photos" in request.POST or "people_layout_present" in request.POST:
        cfg["show_photos"] = request.POST.get("show_photos") == "on"
    if "person_col_span" in request.POST:
        raw_pcs = (request.POST.get("person_col_span") or "").strip()
        cfg["person_col_span"] = int(raw_pcs) if raw_pcs.isdigit() and int(raw_pcs) in (12, 6, 4, 3) else 4
    if "person_card_style" in request.POST:
        pcs = (request.POST.get("person_card_style") or "card").strip()
        cfg["person_card_style"] = pcs if pcs in ("card", "horizontal", "flat") else "card"
    if "video_url" in request.POST:
        cfg["video_url"] = (request.POST.get("video_url") or "").strip()[:300]
    if "gallery_layout" in request.POST:
        gl = (request.POST.get("gallery_layout") or "grid").strip()
        cfg["gallery_layout"] = gl if gl in ("grid", "carousel") else "grid"
    if "gallery_cols" in request.POST:
        gc = (request.POST.get("gallery_cols") or "").strip()
        cfg["gallery_cols"] = int(gc) if gc.isdigit() and int(gc) in (2, 3, 4) else 3
    if "gallery_interval" in request.POST:
        gi = (request.POST.get("gallery_interval") or "").strip()
        cfg["gallery_interval"] = int(gi) if gi.isdigit() and int(gi) in (0, 3000, 5000, 8000) else 5000
    if "gallery_caption_mode" in request.POST:
        gcm = (request.POST.get("gallery_caption_mode") or "below").strip()
        cfg["gallery_caption_mode"] = gcm if gcm in ("below", "overlay", "none") else "below"
    if "gallery_show_thumbs" in request.POST:
        cfg["gallery_show_thumbs"] = (request.POST.get("gallery_show_thumbs") or "1").strip() != "0"
    if "gallery_height" in request.POST:
        gh = (request.POST.get("gallery_height") or "").strip()
        cfg["gallery_height"] = int(gh) if gh.isdigit() and int(gh) in (300, 440, 580) else 440
    if "linked_event_id" in request.POST:
        cfg["linked_event_id"] = (request.POST.get("linked_event_id") or "").strip()[:20]
    if "linked_person_id" in request.POST:
        cfg["linked_person_id"] = (request.POST.get("linked_person_id") or "").strip()[:20]
    if "role_filter" in request.POST:
        cfg["role_filter"] = (request.POST.get("role_filter") or "").strip()[:60]
    if "category_filter" in request.POST:
        cfg["category_filter"] = (request.POST.get("category_filter") or "").strip()[:60]
    if "collection_slug" in request.POST:
        cfg["collection_slug"] = (request.POST.get("collection_slug") or "").strip()[:80]
    if "calendar_category" in request.POST:
        cfg["calendar_category"] = (request.POST.get("calendar_category") or "").strip()[:20]
    if "limit" in request.POST:
        raw_lim = (request.POST.get("limit") or "").strip()
        if raw_lim.isdigit():
            cfg["limit"] = max(1, min(50, int(raw_lim)))
    return cfg


@_editor_required
def cockpit_view(request):
    cfg = SiteConfig.get_solo()
    pages = list(PublicPage.objects.all())
    published_count = sum(1 for p in pages if p.is_published)
    draft_count = len(pages) - published_count

    news_col = None
    recent_news = []
    news_total = 0
    open_submissions = []
    if apps.is_installed("list_kit"):
        try:
            from list_kit.models import Collection, Entry
            from list_kit.services import seed_default_templates

            news_col = Collection.objects.filter(template__content_type="news", is_public=True).first()
            if request.method == "POST" and request.POST.get("action") == "quick_news":
                title = (request.POST.get("title") or "").strip()[:255]
                notes = (request.POST.get("notes") or "").strip()
                if title:
                    if news_col is None:
                        tpl = next((t for t in seed_default_templates() if t.slug == "news-aktuelles"), None)
                        if tpl:
                            news_col = Collection.objects.create(
                                title="Aktuelles & News",
                                slug="news",
                                template=tpl,
                                owner=request.user,
                                responsible_user=request.user,
                                is_public=True,
                            )
                    if news_col is not None:
                        Entry.objects.create(
                            collection=news_col,
                            title=title,
                            notes=notes,
                            visibility="public",
                            created_by=request.user,
                        )
                        messages.success(request, f"News „{title}“ veröffentlicht.")
                        return redirect("site_kit:cockpit")

            news_qs = Entry.objects.filter(collection__template__content_type="news").select_related("collection")
            news_total = news_qs.count()
            recent_news = list(news_qs.order_by("-is_pinned", "-created_at")[:6])
            open_submissions = list(
                Entry.objects.filter(collection__template__content_type="form_inbox", is_completed=False)
                .exclude(status__iexact="Abgelehnt")
                .select_related("collection")
                .order_by("-created_at")[:8]
            )
        except Exception:
            pass

    upcoming_events = []
    events_total = 0
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarEvent

            now = timezone.now()
            reg_counts = _get_event_registration_counts()
            ev_qs = CalendarEvent.objects.filter(is_public=True, end_at__gte=now).select_related("category").order_by("start_at")
            events_total = ev_qs.count()
            for ev in ev_qs[:6]:
                upcoming_events.append({
                    "event": ev,
                    "reg_count": reg_counts.get(str(ev.id), 0),
                })
        except Exception:
            pass

    public_docs_count = 0
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Document
            public_docs_count = Document.objects.filter(is_public=True).count()
        except Exception:
            pass

    return render(
        request,
        "site_kit/cockpit.html",
        {
            "site_cfg": cfg,
            "pages": pages,
            "published_count": published_count,
            "draft_count": draft_count,
            "news_col": news_col,
            "recent_news": recent_news,
            "news_total": news_total,
            "open_submissions": open_submissions,
            "upcoming_events": upcoming_events,
            "events_total": events_total,
            "public_docs_count": public_docs_count,
        },
    )


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
        if "logo_url" in request.POST:
            cfg.logo_url = (request.POST.get("logo_url") or "").strip()[:200]
        if request.FILES.get("logo"):
            cfg.logo = request.FILES["logo"]
        elif request.POST.get("remove_logo") == "1":
            cfg.logo = None
        cw = (request.POST.get("container_width") or cfg.container_width or "standard").strip()
        if cw in ("standard", "wide", "full"):
            cfg.container_width = cw
        cfg.save()
        messages.success(request, "Webseiten-Einstellungen gespeichert.")
        return redirect("site_kit:manage")

    pages = PublicPage.objects.all()
    return render(
        request,
        "site_kit/manage.html",
        {
            "site_cfg": cfg,
            "pages": pages,
            "can_edit_site_config": can_edit_config,
            "container_width_choices": SiteConfig.CONTAINER_WIDTH_CHOICES,
        },
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
def system_page_edit_view(request, slug):
    if slug not in ("news", "termine"):
        raise Http404()
    title = "News" if slug == "news" else "Termine"
    page, _ = PublicPage.objects.get_or_create(
        slug=slug,
        defaults={
            "title": title,
            "is_published": True,
            "show_in_nav": False,
        },
    )
    return redirect("site_kit:page_edit", pk=page.id)


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
    is_admin = bool(getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False))
    if request.method == "POST":
        action = request.POST.get("action") or "save_page"
        if action == "save_events_layout" and page.slug == "termine":
            sc = SiteConfig.get_solo()
            el = (request.POST.get("events_layout") or "row").strip()
            ep = (request.POST.get("events_image_position") or "left").strip()
            sc.events_layout = el if el in ("row", "cards", "stacked") else "row"
            sc.events_image_position = ep if ep in ("left", "right", "top") else "left"
            sc.save(update_fields=["events_layout", "events_image_position"])
            messages.success(request, "Darstellung der öffentlichen Termine gespeichert.")
            return redirect("site_kit:page_edit", pk=page.id)
        if action == "add_block":
            btype = request.POST.get("block_type") or "text_image"
            if btype == "rich_html" and not is_admin:
                return HttpResponseForbidden("Nur Administratoren dürfen HTML-/WYSIWYG-Blöcke anlegen.")
            if btype in dict(PageBlock.BLOCK_TYPE_CHOICES):
                pos = (page.blocks.count() + 1) * 10
                raw_body = (request.POST.get("body") or "").strip()
                if btype == "rich_html":
                    raw_body = _sanitize_rich_html(raw_body)
                PageBlock.objects.create(
                    page=page,
                    block_type=btype,
                    col_span=_parse_col_span(request.POST.get("col_span")),
                    title=(request.POST.get("title") or "").strip()[:180],
                    subtitle=(request.POST.get("subtitle") or "").strip()[:255],
                    body=raw_body,
                    image=request.FILES.get("image"),
                    image_url=(request.POST.get("image_url") or "").strip()[:200],
                    video_file=request.FILES.get("video_file"),
                    cta_label=(request.POST.get("cta_label") or "").strip()[:80],
                    cta_url=(request.POST.get("cta_url") or "").strip()[:255],
                    config_json=_parse_block_layout_config(request),
                    position=pos,
                )
                messages.success(request, "Inhaltsblock hinzugefügt.")
        elif action == "edit_block":
            block = get_object_or_404(PageBlock, pk=request.POST.get("block_id"), page=page)
            block.title = (request.POST.get("title") or "").strip()[:180]
            block.subtitle = (request.POST.get("subtitle") or "").strip()[:255]
            raw_body = (request.POST.get("body") or "").strip()
            if block.block_type == "rich_html":
                if is_admin:
                    block.body = _sanitize_rich_html(raw_body)
                    block.col_span = _parse_col_span(request.POST.get("col_span"))
                    block.config_json = _parse_block_layout_config(request, block.config_json)
                    if "image_url" in request.POST:
                        block.image_url = (request.POST.get("image_url") or "").strip()[:200]
                    if request.FILES.get("image"):
                        block.image = request.FILES["image"]
                    elif request.POST.get("remove_image") == "1":
                        block.image = None
                else:
                    # Redakteure können bei Admin-HTML-Blöcken nur Text/Inhalt ändern, nicht Layout/Struktur
                    block.body = _sanitize_rich_html(raw_body)
            else:
                block.body = raw_body
                block.col_span = _parse_col_span(request.POST.get("col_span"))
                block.config_json = _parse_block_layout_config(request, block.config_json)
                if "image_url" in request.POST:
                    block.image_url = (request.POST.get("image_url") or "").strip()[:200]
                if request.FILES.get("image"):
                    block.image = request.FILES["image"]
                elif request.POST.get("remove_image") == "1":
                    block.image = None
                if request.FILES.get("video_file"):
                    block.video_file = request.FILES["video_file"]
                elif request.POST.get("remove_video") == "1":
                    block.video_file = None
                if "cta_label" in request.POST:
                    block.cta_label = (request.POST.get("cta_label") or "").strip()[:80]
                if "cta_url" in request.POST:
                    block.cta_url = (request.POST.get("cta_url") or "").strip()[:255]
            block.save()
            messages.success(request, "Inhaltsblock aktualisiert.")
        elif action == "move_block":
            bid = request.POST.get("block_id")
            direction = request.POST.get("direction")
            ordered = list(page.blocks.order_by("position", "id"))
            for idx, b in enumerate(ordered):
                if str(b.id) == str(bid):
                    swap_idx = idx - 1 if direction == "up" else idx + 1
                    if 0 <= swap_idx < len(ordered):
                        ordered[idx], ordered[swap_idx] = ordered[swap_idx], ordered[idx]
                        for pos_i, item_b in enumerate(ordered):
                            PageBlock.objects.filter(pk=item_b.id).update(position=(pos_i + 1) * 10)
                    break
        elif action == "delete_block":
            bid = request.POST.get("block_id")
            if bid:
                block = get_object_or_404(PageBlock, pk=bid, page=page)
                if block.block_type == "rich_html" and not is_admin:
                    return HttpResponseForbidden("Nur Administratoren dürfen HTML-/WYSIWYG-Blöcke löschen.")
                block.delete()
                messages.success(request, "Block entfernt.")
        elif action == "duplicate_block":
            bid = request.POST.get("block_id")
            if bid:
                src = get_object_or_404(PageBlock, pk=bid, page=page)
                if src.block_type == "rich_html" and not is_admin:
                    return HttpResponseForbidden("Nur Administratoren dürfen HTML-/WYSIWYG-Blöcke duplizieren.")
                from django.core.files.base import ContentFile
                dup = PageBlock.objects.create(
                    page=page,
                    block_type=src.block_type,
                    col_span=src.col_span,
                    title=(f"{src.title[:172]} (Kopie)" if src.title else ""),
                    subtitle=src.subtitle,
                    body=src.body,
                    cta_label=src.cta_label,
                    cta_url=src.cta_url,
                    image_url=src.image_url,
                    position=src.position + 5,
                    is_visible=src.is_visible,
                    config_json=dict(src.config_json or {}),
                )
                if src.image:
                    try:
                        with src.image.open("rb") as fh:
                            dup.image.save((src.image.name or "block.jpg").rsplit("/", 1)[-1], ContentFile(fh.read()), save=True)
                    except Exception:
                        pass
                if src.video_file:
                    try:
                        with src.video_file.open("rb") as fh:
                            dup.video_file.save((src.video_file.name or "video.mp4").rsplit("/", 1)[-1], ContentFile(fh.read()), save=True)
                    except Exception:
                        pass
                for pos_i, item_b in enumerate(page.blocks.order_by("position", "id")):
                    PageBlock.objects.filter(pk=item_b.id).update(position=(pos_i + 1) * 10)
                messages.success(request, "Block dupliziert.")
                return redirect(f"{reverse('site_kit:page_edit', args=[page.id])}?edit_block={dup.id}")
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

    visible_choices = [
        (k, lbl) for k, lbl in PageBlock.BLOCK_TYPE_CHOICES
        if is_admin or k != "rich_html"
    ]
    domain_roles = []
    domain_person_categories = []
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Category, Role
            domain_roles = list(Role.objects.order_by("position", "name").values_list("name", flat=True))
            domain_person_categories = list(
                Category.for_purpose("person", "general").values_list("name", flat=True)
            )
        except Exception:
            pass
    cal_categories = []
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarCategory
            cal_categories = list(CalendarCategory.objects.order_by("name").values("id", "name"))
        except Exception:
            pass
    form_collections = []
    gallery_collections = []
    if apps.is_installed("list_kit"):
        try:
            from list_kit.models import Collection
            form_collections = list(
                Collection.objects.filter(
                    template__content_type="form_inbox",
                    is_public=True,
                    allow_public_submit=True,
                )
                .order_by("title")
                .values("slug", "title")
            )
            gallery_collections = list(
                Collection.objects.filter(
                    template__use_image=True,
                    is_public=True,
                )
                .order_by("title")
                .values("slug", "title")
            )
        except Exception:
            pass
    public_events = []
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarEvent
            public_events = list(CalendarEvent.objects.filter(is_public=True).order_by("-start_at").values("id", "title", "start_at")[:80])
        except Exception:
            pass
    public_persons = []
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person
            public_persons = list(Person.objects.filter(is_public=True).order_by("last_name", "first_name")[:100])
        except Exception:
            pass
    return render(request, "site_kit/page_editor.html", {
        "page": page,
        "blocks": page.blocks.all(),
        "block_choices": visible_choices,
        "col_choices": PageBlock.COL_SPAN_CHOICES,
        "is_admin": is_admin,
        "domain_roles": domain_roles,
        "domain_person_categories": domain_person_categories,
        "cal_categories": cal_categories,
        "form_collections": form_collections,
        "gallery_collections": gallery_collections,
        "public_events": public_events,
        "public_persons": public_persons,
    })
