from functools import wraps
import html
from html.parser import HTMLParser
import json
import re

escape = html.escape

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import PageBlock, PublicPage, SiteConfig


def _has_site_editor_access(user) -> bool:
    from design_studio.permissions import user_has_role
    return user_has_role(user, "editorial")


def _has_site_config_access(user) -> bool:
    from design_studio.permissions import user_has_role
    return user_has_role(user, "management")


def _editor_required(view_func):
    @login_required
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _has_site_editor_access(request.user):
            return HttpResponseForbidden("Redakteurs- oder Admin-Zugriff erforderlich.")
        return view_func(request, *args, **kwargs)

    return _wrapped


def _get_program_nav_state(nav_keys: set[str]) -> tuple[bool, str]:
    show_default_program_nav = False
    program_nav_label = "Programm"
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import OfferingTemplate, Program
            from cal_kit.services import get_cal_profile_labels
            has_programs = Program.objects.filter(is_public=True).exists()
            if "programm" not in nav_keys and "jahresprogramm" not in nav_keys and (has_programs or OfferingTemplate.objects.filter(show_in_program=True).exists()):
                show_default_program_nav = True
            program_nav_label = "Angebote & Programme" if has_programs else (get_cal_profile_labels().get("program_title") or "Programm")
        except Exception:
            pass
    return show_default_program_nav, program_nav_label


def _build_web_menu_items(cfg: SiteConfig, nav_pages, show_news: bool, show_events: bool, show_program: bool, program_label: str) -> tuple[list[dict], dict]:
    nav_cfg = cfg.nav_config_json if isinstance(cfg.nav_config_json, dict) else {}
    saved_items = nav_cfg.get("items") if isinstance(nav_cfg.get("items"), dict) else {}

    base = [
        {
            "key": "home",
            "url": reverse("site_kit:home"),
            "default_label": cfg.home_nav_label or "Start",
            "default_icon": "bi-house-door",
            "default_display": "icon_only" if cfg.home_nav_mode == "icon" else "icon_text",
            "default_visible": cfg.home_nav_mode != "off",
            "match_url_names": ("home",),
            "page_id": None,
        }
    ]
    for p in nav_pages:
        if p.is_home or p.slug in ("news", "termine", "programm") or p.slug.startswith("programm-"):
            continue
        base.append({
            "key": f"page:{p.slug}",
            "url": reverse("site_kit:page_detail", args=[p.slug]),
            "default_label": p.title,
            "default_icon": "bi-file-earmark-text",
            "default_display": "text_only",
            "default_visible": True,
            "match_url_names": (),
            "page_id": p.id,
        })
    if show_news:
        base.append({
            "key": "news",
            "url": reverse("site_kit:news_list"),
            "default_label": "News",
            "default_icon": "bi-newspaper",
            "default_display": "text_only",
            "default_visible": True,
            "match_url_names": ("news_list", "news_detail", "news_archive"),
            "page_id": None,
        })
    if show_events:
        base.append({
            "key": "termine",
            "url": reverse("site_kit:events_list"),
            "default_label": "Termine",
            "default_icon": "bi-calendar-event",
            "default_display": "text_only",
            "default_visible": True,
            "match_url_names": ("events_list", "event_detail"),
            "page_id": None,
        })
    if show_program:
        base.append({
            "key": "programm",
            "url": reverse("site_kit:annual_program"),
            "default_label": program_label,
            "default_icon": "bi-journal-richtext",
            "default_display": "text_only",
            "default_visible": True,
            "match_url_names": ("annual_program", "program_detail", "program_standard"),
            "page_id": None,
        })
        if apps.is_installed("cal_kit"):
            try:
                from cal_kit.models import OfferingTemplate, Program
                pub_progs = list(Program.objects.filter(is_public=True, show_in_nav=True))
                for prog in pub_progs:
                    base.append({
                        "key": f"program:{prog.slug}",
                        "url": reverse("site_kit:program_detail", args=[prog.slug]),
                        "default_label": prog.title,
                        "default_icon": "bi-journal-richtext",
                        "default_display": "text_only",
                        "default_visible": True,
                        "default_parent_key": "programm",
                        "match_url_names": (),
                        "page_id": None,
                    })
                if pub_progs and OfferingTemplate.objects.filter(show_in_program=True, is_active=True, program__isnull=True).exists():
                    base.append({
                        "key": "program:standard",
                        "url": reverse("site_kit:program_standard"),
                        "default_label": "Allgemeine Angebote",
                        "default_icon": "bi-collection",
                        "default_display": "text_only",
                        "default_visible": True,
                        "default_parent_key": "programm",
                        "match_url_names": (),
                        "page_id": None,
                    })
            except Exception:
                pass

    resolved = []
    for idx, item in enumerate(base):
        ov = saved_items.get(item["key"]) if isinstance(saved_items.get(item["key"]), dict) else {}
        disp = (ov.get("display") or item["default_display"]).strip()
        if disp not in ("text_only", "icon_text", "icon_only"):
            disp = item["default_display"]
        icon = (ov.get("icon") if "icon" in ov else item["default_icon"]) or ""
        label = (ov.get("label") or "").strip()[:40] or item["default_label"]
        visible = bool(ov.get("visible", item["default_visible"]))
        try:
            order = int(ov.get("order", (idx + 1) * 10))
        except (TypeError, ValueError):
            order = (idx + 1) * 10
        raw_parent = ov.get("parent_key") if "parent_key" in ov else item.get("default_parent_key", "")
        parent_key = (raw_parent or "").strip()[:80]
        resolved.append({
            **item,
            "label": label,
            "icon": icon,
            "display": disp,
            "visible": visible,
            "order": order,
            "parent_key": parent_key if parent_key != item["key"] else "",
        })
    resolved.sort(key=lambda x: x["order"])

    style = (nav_cfg.get("style") or "inherit").strip()
    align = (nav_cfg.get("align") or "left").strip()
    active_style = (nav_cfg.get("active_style") or "pill").strip()
    overflow_mode = (nav_cfg.get("overflow_mode") or "wrap").strip()
    logo_size = (nav_cfg.get("logo_size") or "sm").strip()
    logo_shape = (nav_cfg.get("logo_shape") or "rounded").strip()
    logo_bg = (nav_cfg.get("logo_bg") or "none").strip()
    nav_padding = (nav_cfg.get("nav_padding") or "normal").strip()
    style_cfg = {
        "style": style if style in ("inherit", "glass", "gradient", "flat", "solid") else "inherit",
        "align": align if align in ("left", "center", "right") else "left",
        "active_style": active_style if active_style in ("pill", "underline", "bold") else "pill",
        "overflow_mode": overflow_mode if overflow_mode in ("wrap", "auto_more", "single") else "wrap",
        "show_logo": bool(nav_cfg.get("show_logo", True)),
        "show_site_name": bool(nav_cfg.get("show_site_name", True)),
        "brand_title": str(nav_cfg.get("brand_title") or "").strip()[:80],
        "logo_size": logo_size if logo_size in ("sm", "md", "lg", "xl") else "sm",
        "logo_shape": logo_shape if logo_shape in ("rounded", "square", "circle", "pill") else "rounded",
        "logo_bg": logo_bg if logo_bg in ("none", "light_badge", "glass_badge") else "none",
        "nav_padding": nav_padding if nav_padding in ("normal", "compact", "flush", "flush_edge") else "normal",
    }
    return resolved, style_cfg


def get_web_menu_studio_context() -> dict:
    cfg = SiteConfig.get_solo()
    nav_pages = list(PublicPage.objects.filter(is_published=True, show_in_nav=True))
    nav_keys = {p.slug.lower() for p in nav_pages if not p.is_home} | {
        p.title.strip().lower() for p in nav_pages if not p.is_home
    }
    show_program, program_label = _get_program_nav_state(nav_keys)
    items, style_cfg = _build_web_menu_items(
        cfg,
        nav_pages,
        show_news="news" not in nav_keys,
        show_events="termine" not in nav_keys,
        show_program=show_program,
        program_label=program_label,
    )
    return {
        "web_menu_studio_items": items,
        "web_menu_studio_cfg": style_cfg,
    }


def save_web_menu_config(payload: dict) -> None:
    if not isinstance(payload, dict):
        return
    cfg = SiteConfig.get_solo()
    style = (payload.get("style") or "inherit").strip()
    align = (payload.get("align") or "left").strip()
    active_style = (payload.get("active_style") or "pill").strip()
    overflow_mode = (payload.get("overflow_mode") or "wrap").strip()
    raw_items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    clean_items = {}
    for key, val in raw_items.items():
        if not isinstance(val, dict) or not isinstance(key, str):
            continue
        disp = (val.get("display") or "text_only").strip()
        if disp not in ("text_only", "icon_text", "icon_only"):
            disp = "text_only"
        pkey = str(val.get("parent_key") or "").strip()[:80]
        if pkey == key:
            pkey = ""
        clean_items[key[:80]] = {
            "label": str(val.get("label") or "").strip()[:40],
            "icon": str(val.get("icon") or "").strip()[:40],
            "display": disp,
            "parent_key": pkey,
            "visible": bool(val.get("visible", True)),
            "order": int(val.get("order") or 0),
        }
    logo_size = (payload.get("logo_size") or "sm").strip()
    logo_shape = (payload.get("logo_shape") or "rounded").strip()
    logo_bg = (payload.get("logo_bg") or "none").strip()
    nav_padding = (payload.get("nav_padding") or "normal").strip()
    cfg.nav_config_json = {
        "style": style if style in ("inherit", "glass", "gradient", "flat", "solid") else "inherit",
        "align": align if align in ("left", "center", "right") else "left",
        "active_style": active_style if active_style in ("pill", "underline", "bold") else "pill",
        "overflow_mode": overflow_mode if overflow_mode in ("wrap", "auto_more", "single") else "wrap",
        "show_logo": bool(payload.get("show_logo", True)),
        "show_site_name": bool(payload.get("show_site_name", True)),
        "brand_title": str(payload.get("brand_title") or "").strip()[:80],
        "logo_size": logo_size if logo_size in ("sm", "md", "lg", "xl") else "sm",
        "logo_shape": logo_shape if logo_shape in ("rounded", "square", "circle", "pill") else "rounded",
        "logo_bg": logo_bg if logo_bg in ("none", "light_badge", "glass_badge") else "none",
        "nav_padding": nav_padding if nav_padding in ("normal", "compact", "flush", "flush_edge") else "normal",
        "items": clean_items,
    }
    home_ov = clean_items.get("home")
    if isinstance(home_ov, dict):
        if not home_ov.get("visible", True):
            cfg.home_nav_mode = "off"
        elif home_ov.get("display") == "icon_only":
            cfg.home_nav_mode = "icon"
        else:
            cfg.home_nav_mode = "icon_text"
        if home_ov.get("label"):
            cfg.home_nav_label = home_ov["label"]
    cfg.save()


def _public_base_context(request, page: PublicPage | None = None) -> dict:
    request._ds_is_public_website = True
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

    show_default_program_nav, program_nav_label = _get_program_nav_state(nav_keys)
    web_items_flat, web_style = _build_web_menu_items(
        cfg,
        nav_pages,
        show_news="news" not in nav_keys,
        show_events="termine" not in nav_keys,
        show_program=show_default_program_nav,
        program_label=program_nav_label,
    )
    visible_web = [w for w in web_items_flat if w.get("visible")]
    web_by_key = {w["key"]: w for w in visible_web}
    for w in visible_web:
        w["children"] = []
        w["child_page_ids"] = []
        w["child_match_url_names"] = []
    web_items = []
    for w in visible_web:
        pk = w.get("parent_key") or ""
        parent = web_by_key.get(pk) if pk and pk != w["key"] else None
        if parent and not parent.get("parent_key"):
            parent["children"].append(w)
            if w.get("page_id"):
                parent["child_page_ids"].append(w["page_id"])
            parent["child_match_url_names"].extend(w.get("match_url_names") or ())
        else:
            web_items.append(w)

    ctx = {
        "site_cfg": cfg,
        "public_nav_pages": nav_pages,
        "show_default_news_nav": "news" not in nav_keys,
        "show_default_events_nav": "termine" not in nav_keys,
        "show_default_program_nav": show_default_program_nav,
        "program_nav_label": program_nav_label,
        "public_web_menu_items": web_items,
        "public_web_menu_style": web_style,
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
            .exclude(status__iexact="Abgesagt")
            .exclude(status__iexact="cancelled")
            .exclude(status__iexact="warteliste")
            .exclude(status__iexact="waitlist")
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
            sanitized = _sanitize_rich_html(raw)
            sanitized = re.sub(r"</?a\b[^>]*>", "", sanitized, flags=re.IGNORECASE)
            sanitized = re.sub(r"<img\b[^>]*>", "", sanitized, flags=re.IGNORECASE)
            e.preview_html = sanitized
        else:
            e.preview_html = linebreaks(escape(raw))
    return entries


def _resolve_block_data(block: PageBlock, request=None) -> dict:
    cfg = block.config_json if isinstance(block.config_json, dict) else {}
    limit = int(cfg.get("limit") or 6)
    data = {"block": block, "config": cfg}

    if block.block_type == "news_feed" and apps.is_installed("list_kit"):
        try:
            from django.utils.html import strip_tags
            from list_kit.services import get_published_entries
            col_slug = (cfg.get("collection_slug") or "").strip() or None
            n_items = _attach_news_preview_html(
                get_published_entries(content_type="news", collection_slug=col_slug, limit=limit)
            )
            for ne in n_items:
                ne.short_text = " ".join(strip_tags(getattr(ne, "preview_html", "") or getattr(ne, "notes", "") or "").split())
            data["news_items"] = n_items
        except Exception:
            data["news_items"] = []
        data["news_layout"] = cfg.get("news_layout") or "grid"
        nd = (cfg.get("news_density") or "").strip()
        if nd not in ("full", "compact", "single_line"):
            nd = "compact" if block.is_compact else "full"
        data["news_density"] = nd
        data["show_news_images"] = cfg.get("show_news_images", True)
        nif = (cfg.get("news_image_fit") or "contain").strip()
        data["news_image_fit"] = nif if nif in ("contain", "cover") else "contain"

    elif block.block_type == "collection_list" and apps.is_installed("list_kit"):
        try:
            from django.utils.html import strip_tags
            from list_kit.models import Collection
            col_slug = (cfg.get("collection_slug") or "").strip()
            col_obj = None
            if col_slug:
                col_obj = (
                    Collection.objects.select_related("template")
                    .filter(slug=col_slug, is_public=True)
                    .exclude(template__content_type="form_inbox")
                    .first()
                )
            data["list_collection"] = col_obj
            if col_obj is not None:
                from django.db.models import Q as _Q
                entries_qs = (
                    col_obj.entries.exclude(visibility="private")
                    .select_related("collection", "collection__template")
                    .prefetch_related("images")
                )
                ccat = (cfg.get("collection_category") or "").strip()
                if ccat:
                    if ccat.isdigit():
                        entries_qs = entries_qs.filter(category_id=int(ccat))
                    else:
                        entries_qs = entries_qs.filter(_Q(category_label__iexact=ccat))
                csort = (cfg.get("collection_sort") or "position").strip()
                csort_map = {
                    "position": ("-is_pinned", "position", "-created_at"),
                    "title": ("-is_pinned", "title", "-created_at"),
                    "created_desc": ("-is_pinned", "-created_at"),
                    "created_asc": ("-is_pinned", "created_at"),
                    "status": ("-is_pinned", "status", "position", "-created_at"),
                }
                entries = list(entries_qs.order_by(*csort_map.get(csort, csort_map["position"]))[:limit])
                entries = _attach_news_preview_html(entries)
                for ne in entries:
                    ne.short_text = " ".join(strip_tags(getattr(ne, "preview_html", "") or getattr(ne, "notes", "") or "").split())
                data["list_items"] = entries
            else:
                data["list_items"] = []
        except Exception:
            data["list_collection"] = None
            data["list_items"] = []
        data["news_layout"] = cfg.get("news_layout") or "grid"
        nd = (cfg.get("news_density") or "").strip()
        if nd not in ("full", "compact", "single_line"):
            nd = "compact" if block.is_compact else "full"
        data["news_density"] = nd
        data["show_news_images"] = cfg.get("show_news_images", True)
        nif = (cfg.get("news_image_fit") or "contain").strip()
        data["news_image_fit"] = nif if nif in ("contain", "cover") else "contain"

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
        if cfg.get("full_list") or getattr(block.page, "slug", "") == "termine":
            ev_data = _build_events_list_data(request)
            data["full_list"] = True
            data["site_cfg"] = SiteConfig.get_solo()
            data.update(ev_data)
        else:
            try:
                from cal_kit.models import CalendarEvent
                now = timezone.now()
                qs = CalendarEvent.objects.filter(is_public=True, end_at__gte=now)
                cal_cat = (cfg.get("calendar_category") or "").strip()
                if cal_cat.isdigit():
                    qs = qs.filter(category_id=int(cal_cat))
                    data["calendar_category_id"] = cal_cat
                data["events"] = list(qs.order_by("start_at")[: max(1, min(100, limit))])
            except Exception:
                data["events"] = []

    elif block.block_type in ("hero", "text_image"):
        raw_pos = (cfg.get("image_position") or "middle-right").strip()
        pos_norm = {
            "left": "middle-left",
            "right": "middle-right",
            "top": "top-center",
            "bottom": "bottom-center",
        }.get(raw_pos, raw_pos)
        data["image_pos_norm"] = pos_norm

    elif block.block_type == "people_grid" and apps.is_installed("domain_kit"):
        try:
            from django.db.models import Q
            from domain_kit.models import Person

            qs = Person.objects.filter(is_public=True).prefetch_related("roles")
            role_filters = block.role_filters
            if role_filters:
                q_role = Q()
                for rf in role_filters:
                    q_role |= Q(roles__name__icontains=rf)
                qs = qs.filter(q_role).distinct()
            cat_filters = block.category_filters
            if cat_filters:
                q_cat = Q()
                for cf in cat_filters:
                    q_cat |= Q(category__name__icontains=cf)
                qs = qs.filter(q_cat).distinct()
            p_order = (cfg.get("people_order") or "last_name").strip()
            if p_order == "first_name":
                people_list = list(qs.order_by("first_name", "last_name", "id")[:limit])
            elif p_order == "newest":
                people_list = list(qs.order_by("-id")[:limit])
            elif p_order == "oldest":
                people_list = list(qs.order_by("id")[:limit])
            elif p_order == "random":
                import random
                people_list = list(qs)
                random.shuffle(people_list)
                people_list = people_list[:limit]
            else:
                people_list = list(qs.order_by("last_name", "first_name", "id")[:limit])
            data["people"] = _resolve_people_with_courses(people_list)
        except Exception:
            data["people"] = []
        data["show_photos"] = cfg.get("show_photos", True)
        data["person_col_span"] = int(cfg.get("person_col_span") or 4)
        pcs = cfg.get("person_card_style") or "card"
        data["person_card_style"] = pcs
        data["people_layout"] = cfg.get("people_layout") or "grid"
        raw_pos = (cfg.get("image_position") or "").strip()
        if not raw_pos or raw_pos in ("right", "left", "top", "bottom"):
            if pcs == "horizontal":
                raw_pos = "middle-right" if raw_pos == "right" else "middle-left"
            else:
                raw_pos = {"left": "middle-left", "right": "top-center", "top": "top-center", "bottom": "bottom-center"}.get(raw_pos, "top-center")
        data["person_image_position"] = raw_pos

    elif block.block_type == "downloads" and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Document
            data["documents"] = list(Document.objects.filter(is_public=True).order_by("-created_at")[:limit])
        except Exception:
            data["documents"] = []

    elif block.block_type == "program_hub":
        data.update(_build_program_hub_data())

    elif block.block_type == "quick_links":
        data["quick_links"] = cfg.get("links") if isinstance(cfg.get("links"), list) else []
        ll = (cfg.get("links_layout") or "grid_3").strip()
        data["links_layout"] = ll if ll in ("stack", "inline", "grid_2", "grid_3", "grid_4") else "grid_3"
        ls = (cfg.get("links_style") or "cards").strip()
        data["links_style"] = ls if ls in ("cards", "pills", "minimal") else "cards"
        data["show_icons"] = cfg.get("show_icons", True)

    elif block.block_type == "annual_program":
        p_slug = (cfg.get("program_slug") or "").strip() or None
        if not p_slug and getattr(block.page, "slug", "").startswith("programm-"):
            p_slug = block.page.slug[len("programm-"):]
        yr = ((request.GET.get("jahr") or "").strip() if request else "") or str(cfg.get("program_year") or "").strip()
        cid = ((request.GET.get("kategorie") or "").strip() if request else "") or str(cfg.get("calendar_category") or "").strip()
        iref = (request.GET.get("dozent") or "").strip() if request else None
        prog = _build_program_data(
            year=yr or None,
            cat_id=cid or None,
            instructor_ref=iref or None,
            program_slug=p_slug,
        )
        items_list = prog["items"]
        try:
            lim = int(cfg.get("limit") or 0)
        except (TypeError, ValueError):
            lim = 0
        has_more = bool(lim > 0 and len(items_list) > lim)
        if lim > 0:
            items_list = items_list[:lim]
        pl = (cfg.get("program_layout") or "cards").strip()
        is_prog_page = bool(request and getattr(block.page, "slug", "").startswith("programm"))
        data["program_items"] = items_list
        data["program_year"] = prog["year"]
        data["program_title"] = prog["program_title"]
        data["program_obj"] = prog.get("program_obj")
        data["program_description"] = prog.get("program_description", "")
        data["program_slug"] = (p_slug if p_slug != "__all__" else "") or ""
        data["program_layout"] = pl if pl in ("cards", "compact", "carousel") else "cards"
        data["program_has_more"] = has_more or (lim > 0)
        data["show_pdf_button"] = cfg.get("show_pdf_button", True)
        show_filters = cfg.get("show_filters", is_prog_page)
        if show_filters:
            data["program_years"] = prog["years"]
            data["program_categories"] = prog["categories"]
            data["active_category"] = cid or ""

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
        autoplay = bool(cfg.get("video_autoplay", False))
        if autoplay and embed_url:
            sep = "&" if "?" in embed_url else "?"
            embed_url = f"{embed_url}{sep}autoplay=1&mute=1"
        pad = (cfg.get("video_padding") or "normal").strip()
        data["video_src"] = v_url
        data["video_provider"] = provider
        data["video_embed_url"] = embed_url
        data["video_is_file"] = bool(block.video_file) or (bool(v_url) and not embed_url)
        data["video_padding"] = pad if pad in ("normal", "narrow", "none") else "normal"
        data["video_autoplay"] = autoplay

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
        return redirect("site_kit:events_list")
    if slug == "news":
        return redirect("site_kit:news_list")
    if slug == "programm":
        return redirect("site_kit:annual_program")
    if slug.startswith("programm-"):
        return redirect("site_kit:program_detail", program_slug=slug[len("programm-"):])
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
    ctx["has_news_feed_block"] = any(item["block"].block_type == "news_feed" for item in ctx["extra_blocks"])
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


def collection_entry_detail_view(request, pk):
    if not apps.is_installed("list_kit"):
        raise Http404()
    from list_kit.models import Entry
    from list_kit.services import get_linked_domain_document, render_safe_markdown

    qs = (
        Entry.objects.select_related("collection", "collection__template", "created_by")
        .prefetch_related("images")
        .exclude(collection__template__content_type="form_inbox")
    )
    if not (request.user.is_authenticated and request.user.is_staff):
        entry = get_object_or_404(qs.exclude(visibility="private"), pk=pk, collection__is_public=True)
    else:
        entry = get_object_or_404(qs, pk=pk)

    ctx = _public_base_context(request)
    ctx["entry"] = entry
    raw_body = (entry.notes or "").strip()
    if entry.collection.template.use_rich_notes or ("<" in raw_body and ">" in raw_body):
        ctx["rendered_body"] = _sanitize_rich_html(raw_body)
    else:
        ctx["rendered_body"] = render_safe_markdown(raw_body)
    ctx["custom_display"] = entry.get_custom_field_display()
    ctx["public_documents"] = list(entry.documents.filter(is_public=True))
    ctx["linked_document"] = get_linked_domain_document(entry.linked_document_id, require_public=True) if entry.linked_document_id else None
    return render(request, "site_kit/entry_detail.html", ctx)


def _build_events_list_data(request=None) -> dict:
    events = []
    categories = []
    grouped_events = []
    event_years = []
    active_year = None
    active_calendar = (request.GET.get("kalender") or "").strip() if request else ""
    raw_year = (request.GET.get("jahr") or "").strip() if request else ""
    if raw_year.isdigit():
        active_year = int(raw_year)
    cfg = SiteConfig.get_solo()

    if apps.is_installed("cal_kit"):
        try:
            from datetime import datetime, timedelta
            from cal_kit.models import CalendarCategory, CalendarEvent

            now = timezone.now()
            tz = timezone.get_current_timezone()
            public_qs = (
                CalendarEvent.objects.filter(is_public=True)
                .select_related("category", "offering_template")
                .order_by("start_at")
            )
            ev_year_dates = set(public_qs.dates("start_at", "year"))
            event_years = sorted({d.year for d in ev_year_dates} | {now.year})
            if active_year:
                window_start = datetime(active_year, 1, 1, 0, 0, 0, tzinfo=tz)
                window_end = datetime(active_year, 12, 31, 23, 59, 59, tzinfo=tz)
            else:
                window_start = now
                window_end = now + timedelta(days=180)

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
                    if ev.end_at >= window_start and ev.start_at <= window_end:
                        expanded.append(_ev_payload(ev, ev.start_at, ev.end_at))
                else:
                    for occ_start, occ_end, _occ_title in ev.get_occurrences(window_start, window_end):
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

            if getattr(cfg, "events_group_by_category", True):
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
            elif events:
                grouped_events = [{
                    "key": "all",
                    "name": "Kommende Termine",
                    "color": "#2563eb",
                    "icon": "bi-calendar-event",
                    "items": events,
                }]
        except Exception:
            import logging
            logging.getLogger(__name__).exception("events_list_view failed")
            events = []

    return {
        "events": events,
        "categories": categories,
        "grouped_events": grouped_events,
        "active_calendar": active_calendar,
        "event_years": event_years,
        "active_year": active_year,
        "total_public_events": sum(c["count"] for c in categories),
    }


def _ensure_system_page(slug: str) -> PublicPage:
    titles = {"news": "News", "termine": "Termine", "programm": "Angebote & Programme"}
    title = titles.get(slug)
    if not title and slug.startswith("programm-") and apps.is_installed("cal_kit"):
        from cal_kit.models import Program
        prog = Program.objects.filter(slug=slug[len("programm-"):]).first()
        title = prog.title if prog else "Programm"
    page, _ = PublicPage.objects.get_or_create(
        slug=slug,
        defaults={
            "title": (title or slug)[:160],
            "is_published": True,
            "show_in_nav": False,
        },
    )
    if not page.blocks.exists():
        if slug == "news":
            PageBlock.objects.create(
                page=page,
                block_type="news_feed",
                col_span=12,
                title="Aktuelles & News",
                config_json={"limit": 18, "style_variant": "card"},
                position=10,
            )
        elif slug == "termine":
            PageBlock.objects.create(
                page=page,
                block_type="upcoming_events",
                col_span=12,
                title="Öffentliche Termine",
                config_json={"limit": 80, "full_list": True, "style_variant": "card"},
                position=10,
            )
        elif slug == "programm":
            has_progs = False
            if apps.is_installed("cal_kit"):
                from cal_kit.models import Program
                has_progs = Program.objects.filter(is_public=True).exists()
            PageBlock.objects.create(
                page=page,
                block_type="program_hub" if has_progs else "annual_program",
                col_span=12,
                title="Angebote & Programme" if has_progs else "",
                config_json={},
                position=10,
            )
        elif slug.startswith("programm-"):
            PageBlock.objects.create(
                page=page,
                block_type="annual_program",
                col_span=12,
                title="",
                config_json={"program_slug": slug[len("programm-"):]},
                position=10,
            )
    return page


def events_list_view(request):
    sys_page = _ensure_system_page("termine")
    ctx = _public_base_context(request, page=sys_page)
    ctx["blocks"] = [_resolve_block_data(b, request=request) for b in sys_page.blocks.filter(is_visible=True)]
    return render(request, "site_kit/page.html", ctx)


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
        "fee_amount": ev.fee_amount if ev.fee_amount is not None else (tpl.fee_amount if tpl else None),
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


def _build_program_data(year=None, cat_id=None, instructor_ref=None, program_slug=None) -> dict:
    items = []
    categories = []
    instructors = []
    years = []
    program_title = "Jahresprogramm"
    program_obj = None
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
            "program_obj": None,
            "program_description": "",
        }

    try:
        from cal_kit.models import CalendarCategory, CalendarEvent, OfferingTemplate, Program
        from cal_kit.services import get_cal_profile_labels

        program_title = get_cal_profile_labels().get("program_title") or "Jahresprogramm"
        if program_slug == "__all__":
            program_obj = None
        elif program_slug:
            program_obj = Program.objects.filter(slug=program_slug, is_public=True).first()
            if not program_obj:
                return {
                    "items": [],
                    "categories": [],
                    "instructors": [],
                    "years": [now.year],
                    "year": sel_year or now.year,
                    "program_title": program_title,
                    "program_obj": None,
                    "program_description": "",
                    "not_found": True,
                }
            program_title = program_obj.title
        else:
            program_obj = (
                Program.objects.filter(slug="jahresprogramm", is_public=True).first()
                or Program.objects.filter(is_public=True).order_by("position", "id").first()
            )
            if program_obj:
                program_title = program_obj.title

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

        base_qs = OfferingTemplate.objects.filter(show_in_program=True, is_active=True)
        if program_slug == "__all__":
            from django.db.models import Q as _Q
            base_qs = base_qs.filter(_Q(program__is_public=True) | _Q(program__isnull=True))
        elif program_obj:
            if program_obj.slug == "jahresprogramm":
                from django.db.models import Q as _Q
                base_qs = base_qs.filter(_Q(program=program_obj) | _Q(program__isnull=True))
            else:
                base_qs = base_qs.filter(program=program_obj)
        else:
            base_qs = base_qs.filter(program__isnull=True)

        qs = base_qs.select_related("category").order_by("position", "title", "id")
        if cat_id and str(cat_id).isdigit():
            qs = qs.filter(category_id=int(cat_id))
        if instructor_ref:
            qs = qs.filter(instructor_ref=instructor_ref)

        seen_instructors = {}
        for tpl in base_qs.order_by("instructor_name"):
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
        "program_obj": program_obj,
        "program_description": program_obj.description if program_obj else "",
    }


@login_required
def offering_preview_view(request, pk):
    if not apps.is_installed("cal_kit"):
        raise Http404("cal_kit ist nicht installiert.")
    from cal_kit.models import OfferingTemplate

    tpl = get_object_or_404(OfferingTemplate.objects.select_related("category"), pk=pk)
    can_view = _has_site_editor_access(request.user) or tpl.user_id == request.user.id
    if not can_view:
        try:
            from cal_kit.views import _can_write_calendar
            can_view = _can_write_calendar(request.user)
        except Exception:
            can_view = False
    if not can_view:
        return HttpResponseForbidden("Kein Zugriff auf die Vorschau.")

    sys_page = PublicPage.objects.filter(slug="programm").first()
    ctx = _public_base_context(request, page=sys_page)
    now = timezone.now()
    reg_counts = _get_event_registration_counts()
    people_map = {}
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Person
            for p in Person.objects.all():
                people_map[f"person:{p.id}"] = p
        except Exception:
            pass

    ev_rows = []
    for ev in tpl.events.filter(is_public=True).order_by("start_at"):
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

    ctx.update({
        "preview_offering": tpl,
        "program_items": [{
            "offering": tpl,
            "description_html": d_html,
            "events": ev_rows,
            "instructor_person": inst_person if (inst_person and inst_person.is_public) else None,
            "responsible_person": resp_person,
        }],
    })
    return render(request, "site_kit/offering_preview.html", ctx)


def _build_program_hub_data() -> dict:
    if not apps.is_installed("cal_kit"):
        return {
            "featured_programs": [],
            "browse_programs": [],
            "hub_layout": "cards_large",
            "standard_count": 0,
            "upcoming_total": 0,
        }
    from cal_kit.models import CalendarEvent, OfferingTemplate, Program

    now = timezone.now()
    progs = list(Program.objects.filter(is_public=True).order_by("position", "title", "id"))
    featured_programs = []
    browse_programs = []
    for p in progs:
        off_qs = OfferingTemplate.objects.filter(program=p, is_active=True, show_in_program=True)
        ev_list = list(
            CalendarEvent.objects.filter(
                offering_template__program=p,
                offering_template__is_active=True,
                is_public=True,
                end_at__gte=now,
            )
            .order_by("start_at")[:3]
        )
        entry = {
            "program": p,
            "offering_count": off_qs.count(),
            "upcoming_events": ev_list,
            "url": reverse("site_kit:program_detail", args=[p.slug]),
        }
        if p.is_featured:
            featured_programs.append(entry)
        else:
            browse_programs.append(entry)

    standard_count = OfferingTemplate.objects.filter(
        program__isnull=True, is_active=True, show_in_program=True
    ).count()
    upcoming_total = CalendarEvent.objects.filter(is_public=True, end_at__gte=now).count()
    cfg = SiteConfig.get_solo()
    return {
        "featured_programs": featured_programs,
        "browse_programs": browse_programs,
        "hub_layout": getattr(cfg, "program_hub_layout", "cards_large") or "cards_large",
        "standard_count": standard_count,
        "upcoming_total": upcoming_total,
    }


def _render_program_hub(request):
    sys_page = _ensure_system_page("programm")
    ctx = _public_base_context(request, page=sys_page)
    ctx.update(_build_program_hub_data())
    return render(request, "site_kit/program_hub.html", ctx)


def program_standard_view(request):
    if apps.is_installed("cal_kit"):
        from cal_kit.models import Program
        jp = Program.objects.filter(slug="jahresprogramm", is_public=True).first()
        if jp:
            return redirect("site_kit:program_detail", program_slug=jp.slug)
    return annual_program_view(request, program_slug=None, force_standard=True)


def annual_program_view(request, program_slug=None, force_standard=False):
    if not force_standard:
        if program_slug:
            if not apps.is_installed("cal_kit"):
                raise Http404()
            from cal_kit.models import Program
            if not Program.objects.filter(slug=program_slug, is_public=True).exists():
                raise Http404()
            sys_page = _ensure_system_page(f"programm-{program_slug}")
        else:
            sys_page = _ensure_system_page("programm")
            if apps.is_installed("cal_kit"):
                from cal_kit.models import Program
                if (
                    Program.objects.filter(is_public=True).exists()
                    and sys_page.blocks.filter(block_type="annual_program").exists()
                    and not sys_page.blocks.filter(block_type="program_hub").exists()
                    and sys_page.blocks.count() == 1
                ):
                    sys_page.blocks.filter(block_type="annual_program").update(
                        block_type="program_hub", title="Angebote & Programme"
                    )
        ctx = _public_base_context(request, page=sys_page)
        ctx["blocks"] = [_resolve_block_data(b, request=request) for b in sys_page.blocks.filter(is_visible=True)]
        return render(request, "site_kit/page.html", ctx)
    year = (request.GET.get("jahr") or "").strip()
    cat_id = (request.GET.get("kategorie") or "").strip()
    inst_ref = (request.GET.get("dozent") or "").strip()
    prog = _build_program_data(
        year=year or None,
        cat_id=cat_id or None,
        instructor_ref=inst_ref or None,
        program_slug=program_slug,
    )
    if prog.get("not_found"):
        raise Http404()
    page_slug = f"programm-{program_slug}" if program_slug else "programm"
    sys_page = PublicPage.objects.filter(slug=page_slug).first()
    ctx = _public_base_context(request, page=sys_page)
    ctx["extra_blocks"] = [_resolve_block_data(b) for b in sys_page.blocks.filter(is_visible=True)] if sys_page else []
    ctx.update({
        "program_items": prog["items"],
        "program_categories": prog["categories"],
        "program_instructors": prog["instructors"],
        "program_years": prog["years"],
        "active_year": prog["year"],
        "active_category": cat_id,
        "active_instructor": inst_ref,
        "program_title": prog["program_title"],
        "program_obj": prog.get("program_obj"),
        "program_description": prog.get("program_description", ""),
        "program_slug": program_slug or "",
    })
    return render(request, "site_kit/annual_program.html", ctx)


def annual_program_pdf_view(request, program_slug=None):
    from io import BytesIO
    from django.template.loader import get_template

    ctx = _public_base_context(request)
    year = (request.GET.get("jahr") or "").strip()
    cat_id = (request.GET.get("kategorie") or "").strip()
    inst_ref = (request.GET.get("dozent") or "").strip()
    prog = _build_program_data(
        year=year or None,
        cat_id=cat_id or None,
        instructor_ref=inst_ref or None,
        program_slug=program_slug,
    )
    if prog.get("not_found"):
        raise Http404()
    ctx.update({
        "program_items": prog["items"],
        "active_year": prog["year"],
        "program_title": prog["program_title"],
    })
    try:
        from design_studio.branding import get_branding_context, pdf_link_callback
        ctx["branding"] = get_branding_context("pdf")
    except Exception:
        pdf_link_callback = None
        ctx["branding"] = {"org_name": ctx["site_cfg"].site_name, "primary_color": "#2563eb", "channel": {"show_logo": True, "show_letterhead": True, "use_brand_color": True, "show_footer": True}}
    html = get_template("site_kit/annual_program_pdf.html").render(ctx)
    try:
        from xhtml2pdf import pisa
        result = BytesIO()
        pdf = pisa.pisaDocument(BytesIO(html.encode("UTF-8")), result, link_callback=pdf_link_callback)
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


def _resolve_form_header_text(raw_text: str) -> str:
    """Replace organization and bank placeholders using design_studio branding and domain_kit accounts."""
    text = (raw_text or "").strip()
    branding = {}
    if apps.is_installed("design_studio"):
        try:
            from design_studio.branding import get_branding_context
            branding = get_branding_context("print")
        except Exception:
            branding = {}

    bank_lines = []
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import AssetItem
            for acc in AssetItem.objects.filter(asset_type="account").order_by("title"):
                ident = (acc.identifier or "").strip()
                if ident:
                    bank_lines.append(f"{acc.title}: {ident}")
                elif acc.title:
                    bank_lines.append(acc.title)
        except Exception:
            pass
    if not bank_lines and branding.get("iban"):
        line = f"IBAN: {branding['iban']}"
        if branding.get("bic"):
            line += f" · BIC: {branding['bic']}"
        bank_lines.append(line)

    org_name = (branding.get("org_name") or "").strip()
    if not org_name:
        try:
            org_name = (SiteConfig.get_solo().site_name or "").strip()
        except Exception:
            org_name = ""

    if not text:
        parts = [p for p in (org_name, branding.get("address"), branding.get("website")) if p]
        return " · ".join(parts)

    bank_str = "\n".join(bank_lines) if bank_lines else "—"
    replacements = {
        "[[ORG_NAME]]": org_name,
        "[[ORG_ADDRESS]]": branding.get("address") or "",
        "[[ORG_EMAIL]]": branding.get("email") or "",
        "[[ORG_WEBSITE]]": branding.get("website") or "",
        "[[ORG_PHONE]]": branding.get("phone") or "",
        "[[ORG_IBAN]]": branding.get("iban") or "",
        "[[ORG_BIC]]": branding.get("bic") or "",
        "[[BANKVERBINDUNGEN]]": bank_str,
        "{{ vereinsname }}": org_name,
        "{{ organisationsname }}": org_name,
        "{{ adresse }}": branding.get("address") or "",
        "{{ webseite }}": branding.get("website") or "",
        "{{ email }}": branding.get("email") or "",
        "{{ telefon }}": branding.get("phone") or "",
        "{{ iban }}": branding.get("iban") or "",
        "{{ bic }}": branding.get("bic") or "",
        "{{ bankverbindungen }}": bank_str,
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def public_form_view(request, slug):
    if not apps.is_installed("list_kit"):
        raise Http404()
    from list_kit.models import Collection

    col = get_object_or_404(
        Collection.objects.select_related("template"),
        slug=slug,
        template__content_type="form_inbox",
    )
    ctx = _public_base_context(request)
    ctx["form_collection"] = col
    ctx["form_header_text"] = _resolve_form_header_text(col.template.description)
    return render(request, "site_kit/public_form.html", ctx)


def imprint_view(request):
    from .legal import get_default_imprint_template, render_legal_text

    ctx = _public_base_context(request)
    ctx["legal_title"] = "Impressum"
    raw = (ctx["site_cfg"].imprint_text or "").strip() or get_default_imprint_template()
    ctx["legal_body"] = render_legal_text(raw, ctx["site_cfg"], request=request)
    return render(request, "site_kit/legal.html", ctx)


def privacy_view(request):
    from .legal import DEFAULT_PRIVACY_TEMPLATE, render_legal_text

    ctx = _public_base_context(request)
    ctx["legal_title"] = "Datenschutzerklärung"
    raw = (ctx["site_cfg"].privacy_text or "").strip() or DEFAULT_PRIVACY_TEMPLATE
    ctx["legal_body"] = render_legal_text(raw, ctx["site_cfg"], request=request)
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
_STRIP_VOID_TAGS = {"embed"}
_STRIP_WITH_CONTENT_TAGS = {"script", "style", "iframe", "object", "svg", "form"}
_ALLOWED_STYLE_PROPS = {
    "color", "background-color", "text-align", "font-weight", "font-style",
    "text-decoration", "max-width", "width", "height", "border-radius",
}


def _sanitize_safe_url(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    low = val.lower()
    if low.startswith(("http://", "https://", "mailto:", "tel:", "#")):
        return val
    if val.startswith("/") and not val.startswith("//"):
        return val
    return ""


def _sanitize_inline_style(raw_style: str) -> str:
    safe_parts = []
    for decl in (raw_style or "").split(";"):
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop_l = prop.strip().lower()
        val_s = val.strip()
        if prop_l not in _ALLOWED_STYLE_PROPS or not val_s or "\\" in val_s:
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
        if t in _STRIP_VOID_TAGS:
            return
        if t in _STRIP_WITH_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0 or t not in _ALLOWED_HTML_TAGS:
            return
        clean_attrs = []
        for k, v in attrs:
            kl = (k or "").lower()
            if kl == "class" and v:
                safe_cls = re.sub(r"[^A-Za-z0-9 _-]", "", v).strip()
                if safe_cls:
                    clean_attrs.append(f'class="{html.escape(safe_cls, quote=True)}"')
            elif kl == "style" and v:
                safe_style = _sanitize_inline_style(v)
                if safe_style:
                    clean_attrs.append(f'style="{html.escape(safe_style, quote=True)}"')
            elif t == "font" and kl == "color" and v:
                safe_col = _sanitize_inline_style(f"color: {v}")
                if safe_col:
                    clean_attrs.append(f'style="{html.escape(safe_col, quote=True)}"')
            elif t == "a" and kl == "href" and v:
                vl = _sanitize_safe_url(v)
                if vl:
                    clean_attrs.append(f'href="{html.escape(vl, quote=True)}"')
            elif t == "a" and kl == "target" and v in ("_blank", "_self"):
                clean_attrs.append(f'target="{v}"')
            elif t == "img" and kl == "src" and v:
                vl = v.strip()
                if vl.startswith(("http://", "https://", "data:image/")) or (vl.startswith("/") and not vl.startswith("//")):
                    clean_attrs.append(f'src="{html.escape(vl, quote=True)}"')
            elif t == "img" and kl == "alt" and v is not None:
                clean_attrs.append(f'alt="{html.escape(v, quote=True)}"')
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
        return val if val in (12, 9, 8, 6, 4, 3) else 12
    except (TypeError, ValueError):
        return 12


def _parse_block_layout_config(request, existing_cfg=None) -> dict:
    cfg = dict(existing_cfg) if isinstance(existing_cfg, dict) else {}
    sv = (request.POST.get("style_variant") or cfg.get("style_variant") or "card").strip()
    cfg["style_variant"] = (
        sv if sv in ("card", "solid", "flat", "flat_solid", "subtle", "primary", "bg_image") else "card"
    )
    if "bg_color" in request.POST:
        raw_bgc = (request.POST.get("bg_color") or "").strip()
        if len(raw_bgc) == 7 and raw_bgc.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in raw_bgc[1:]):
            cfg["bg_color"] = raw_bgc.lower()
        else:
            cfg["bg_color"] = ""
    if "bg_image_url" in request.POST:
        cfg["bg_image_url"] = (request.POST.get("bg_image_url") or "").strip()[:300]
    if "bg_overlay" in request.POST:
        bo = (request.POST.get("bg_overlay") or "dark").strip()
        cfg["bg_overlay"] = bo if bo in ("dark", "light", "none") else "dark"
    ip = (request.POST.get("image_position") or cfg.get("image_position") or "middle-right").strip()
    valid_ip = (
        "right", "left", "top", "bottom",
        "top-left", "top-center", "top-right",
        "middle-left", "middle-center", "middle-right",
        "bottom-left", "bottom-center", "bottom-right",
    )
    cfg["image_position"] = ip if ip in valid_ip else "middle-right"
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
    if "people_layout" in request.POST:
        pl = (request.POST.get("people_layout") or "grid").strip()
        cfg["people_layout"] = pl if pl in ("grid", "carousel") else "grid"
    if "people_order" in request.POST:
        po = (request.POST.get("people_order") or "last_name").strip()
        cfg["people_order"] = po if po in ("last_name", "first_name", "newest", "oldest", "random") else "last_name"
    if "news_layout" in request.POST:
        nl = (request.POST.get("news_layout") or "grid").strip()
        cfg["news_layout"] = nl if nl in ("grid", "carousel") else "grid"
    if "news_density" in request.POST:
        nd = (request.POST.get("news_density") or "full").strip()
        cfg["news_density"] = nd if nd in ("full", "compact", "single_line") else "full"
    if "news_image_fit" in request.POST:
        nif = (request.POST.get("news_image_fit") or "contain").strip()
        cfg["news_image_fit"] = nif if nif in ("contain", "cover") else "contain"
    if "news_options_present" in request.POST:
        cfg["show_news_images"] = request.POST.get("show_news_images") == "on"
    if "quick_links_present" in request.POST:
        ll = (request.POST.get("links_layout") or "grid_3").strip()
        cfg["links_layout"] = ll if ll in ("stack", "inline", "grid_2", "grid_3", "grid_4") else "grid_3"
        ls = (request.POST.get("links_style") or "cards").strip()
        cfg["links_style"] = ls if ls in ("cards", "pills", "minimal") else "cards"
        cfg["show_icons"] = request.POST.get("show_icons") == "on"
        raw_links = (request.POST.get("quick_links_json") or "").strip()
        parsed_links = []
        if raw_links:
            try:
                loaded = json.loads(raw_links)
                if isinstance(loaded, list):
                    for item in loaded[:24]:
                        if not isinstance(item, dict):
                            continue
                        lbl = str(item.get("label") or "").strip()[:80]
                        url = str(item.get("url") or "").strip()[:300]
                        if lbl and url:
                            acc = str(item.get("accent") or "primary").strip()
                            parsed_links.append({
                                "label": lbl,
                                "subtitle": str(item.get("subtitle") or "").strip()[:140],
                                "url": url,
                                "icon": str(item.get("icon") or "").strip()[:48],
                                "accent": acc if acc in ("primary", "secondary", "success", "info", "warning", "danger") else "primary",
                                "new_tab": bool(item.get("new_tab")),
                            })
            except Exception:
                pass
        elif "ql_label" in request.POST:
            lbls = request.POST.getlist("ql_label")
            urls = request.POST.getlist("ql_url")
            subs = request.POST.getlist("ql_subtitle")
            icons = request.POST.getlist("ql_icon")
            accs = request.POST.getlist("ql_accent")
            tabs = request.POST.getlist("ql_new_tab")
            for idx, lbl in enumerate(lbls[:24]):
                lbl_c = (lbl or "").strip()[:80]
                url_c = (urls[idx] if idx < len(urls) else "").strip()[:300]
                if lbl_c and url_c:
                    acc = (accs[idx] if idx < len(accs) else "primary").strip()
                    nt_val = (tabs[idx] if idx < len(tabs) else "").strip()
                    parsed_links.append({
                        "label": lbl_c,
                        "subtitle": (subs[idx] if idx < len(subs) else "").strip()[:140],
                        "url": url_c,
                        "icon": (icons[idx] if idx < len(icons) else "").strip()[:48],
                        "accent": acc if acc in ("primary", "secondary", "success", "info", "warning", "danger") else "primary",
                        "new_tab": nt_val in ("1", "true", "on", "yes"),
                    })
        cfg["links"] = parsed_links
    if "video_url" in request.POST:
        cfg["video_url"] = (request.POST.get("video_url") or "").strip()[:300]
        vp = (request.POST.get("video_padding") or "normal").strip()
        cfg["video_padding"] = vp if vp in ("normal", "narrow", "none") else "normal"
        cfg["video_autoplay"] = request.POST.get("video_autoplay") in ("1", "on", "true")
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
    if "role_filters" in request.POST or "people_layout_present" in request.POST or "role_filter" in request.POST:
        r_vals = [v.strip()[:60] for v in request.POST.getlist("role_filters") if v.strip()]
        if not r_vals and (request.POST.get("role_filter") or "").strip():
            r_vals = [(request.POST.get("role_filter") or "").strip()[:60]]
        cfg["role_filters"] = r_vals
        cfg["role_filter"] = ", ".join(r_vals)[:60]
    if "category_filters" in request.POST or "people_layout_present" in request.POST or "category_filter" in request.POST:
        c_vals = [v.strip()[:60] for v in request.POST.getlist("category_filters") if v.strip()]
        if not c_vals and (request.POST.get("category_filter") or "").strip():
            c_vals = [(request.POST.get("category_filter") or "").strip()[:60]]
        cfg["category_filters"] = c_vals
        cfg["category_filter"] = ", ".join(c_vals)[:60]
    if "collection_slug" in request.POST:
        cfg["collection_slug"] = (request.POST.get("collection_slug") or "").strip()[:80]
    if "collection_sort" in request.POST:
        cs = (request.POST.get("collection_sort") or "position").strip()
        cfg["collection_sort"] = cs if cs in ("position", "title", "created_desc", "created_asc", "status") else "position"
    if "collection_category" in request.POST:
        cfg["collection_category"] = (request.POST.get("collection_category") or "").strip()[:60]
    if "calendar_category" in request.POST:
        cfg["calendar_category"] = (request.POST.get("calendar_category") or "").strip()[:20]
    if "limit" in request.POST:
        raw_lim = (request.POST.get("limit") or "").strip()
        if raw_lim == "0" or raw_lim == "":
            cfg["limit"] = 0
        elif raw_lim.isdigit():
            cfg["limit"] = max(1, min(100, int(raw_lim)))
    if "program_slug" in request.POST or "program_layout_present" in request.POST:
        cfg["program_slug"] = (request.POST.get("program_slug") or "").strip()[:80]
        cfg["program_year"] = (request.POST.get("program_year") or "").strip()[:10]
        pl = (request.POST.get("program_layout") or "cards").strip()
        cfg["program_layout"] = pl if pl in ("cards", "compact", "carousel") else "cards"
        cfg["show_filters"] = request.POST.get("show_filters") == "on"
        cfg["show_pdf_button"] = request.POST.get("show_pdf_button") == "on"
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
        if request.POST.get("action") == "save_events_settings":
            el = (request.POST.get("events_layout") or "row").strip()
            ep = (request.POST.get("events_image_position") or "left").strip()
            cfg.events_layout = el if el in ("row", "cards", "stacked") else "row"
            cfg.events_image_position = ep if ep in ("left", "right", "top", "none") else "left"
            cfg.events_show_description = request.POST.get("events_show_description") == "on"
            cfg.events_group_by_category = request.POST.get("events_group_by_category") == "on"
            cfg.save(update_fields=["events_layout", "events_image_position", "events_show_description", "events_group_by_category"])
            messages.success(request, "Einstellungen für die Terminseite (/termine/) gespeichert.")
            return redirect("site_kit:manage")
        if not can_edit_config:
            return HttpResponseForbidden("Nur Leitung oder Administrator darf globale Webseiten-Einstellungen ändern.")
        if request.POST.get("action") == "reset_legal_defaults":
            from .legal import DEFAULT_IMPRINT_TEMPLATE, DEFAULT_PRIVACY_TEMPLATE
            cfg.imprint_text = DEFAULT_IMPRINT_TEMPLATE
            cfg.privacy_text = DEFAULT_PRIVACY_TEMPLATE
            cfg.save(update_fields=["imprint_text", "privacy_text"])
            messages.success(request, "Rechtssichere Standard-Vorlagen für Impressum & Datenschutz geladen.")
            return redirect("site_kit:manage")
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
        if cw in ("narrow", "standard", "wide", "full"):
            cfg.container_width = cw
        pm = (request.POST.get("page_margin") or cfg.page_margin or "normal").strip()
        if pm in ("minimal", "compact", "normal", "wide"):
            cfg.page_margin = pm
        hnm = (request.POST.get("home_nav_mode") or cfg.home_nav_mode or "icon_text").strip()
        if hnm in ("icon_text", "icon", "off"):
            cfg.home_nav_mode = hnm
        if "home_nav_label" in request.POST:
            cfg.home_nav_label = (request.POST.get("home_nav_label") or "Start").strip()[:40] or "Start"
        cfg.save()
        messages.success(request, "Webseiten-Einstellungen gespeichert.")
        return redirect("site_kit:manage")

    for sys_slug in ("news", "termine", "programm"):
        _ensure_system_page(sys_slug)
    pages = PublicPage.objects.all()
    programs = []
    program_card_mode_choices = []
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import Program
            programs = list(Program.objects.all())
            program_card_mode_choices = Program.CARD_MODE_CHOICES
        except Exception:
            pass
    return render(
        request,
        "site_kit/manage.html",
        {
            "site_cfg": cfg,
            "pages": pages,
            "programs": programs,
            "program_card_mode_choices": program_card_mode_choices,
            "can_edit_site_config": can_edit_config,
            "container_width_choices": SiteConfig.CONTAINER_WIDTH_CHOICES,
            "page_margin_choices": SiteConfig.PAGE_MARGIN_CHOICES,
            "home_nav_mode_choices": SiteConfig.HOME_NAV_MODE_CHOICES,
            "program_hub_layout_choices": SiteConfig.PROGRAM_HUB_LAYOUT_CHOICES,
        },
    )


@_editor_required
def program_manage_view(request):
    if request.method == "POST" and apps.is_installed("cal_kit"):
        from cal_kit.models import Program
        action = (request.POST.get("action") or "create").strip()
        if action == "set_hub_layout":
            cfg = SiteConfig.get_solo()
            hl = (request.POST.get("program_hub_layout") or "cards_large").strip()
            if hl in dict(SiteConfig.PROGRAM_HUB_LAYOUT_CHOICES):
                cfg.program_hub_layout = hl
                cfg.save(update_fields=["program_hub_layout"])
                messages.success(request, "Darstellung der Übersichtsseite gespeichert.")
        elif action == "create":
            title = (request.POST.get("title") or "").strip()[:120]
            if title:
                try:
                    pos = int(request.POST.get("position") or 0)
                except (TypeError, ValueError):
                    pos = 0
                p = Program.objects.create(
                    title=title,
                    description=(request.POST.get("description") or "").strip(),
                    card_mode=(request.POST.get("card_mode") or "equal_max").strip(),
                    show_in_nav=request.POST.get("show_in_nav") == "on",
                    is_public=request.POST.get("is_public") == "on",
                    is_featured=request.POST.get("is_featured") == "on",
                    position=pos,
                )
                messages.success(request, f"Programm „{p.title}“ erstellt.")
        elif action == "update":
            from django.utils.text import slugify
            prog = get_object_or_404(Program, pk=request.POST.get("program_id"))
            old_slug = prog.slug
            title = (request.POST.get("title") or "").strip()[:120]
            if title:
                prog.title = title
            if "slug" in request.POST:
                raw_slug = (request.POST.get("slug") or "").strip()
                base_slug = slugify(raw_slug) if raw_slug else (slugify(prog.title) or "programm")
                cand = base_slug[:160]
                idx = 2
                while Program.objects.filter(slug=cand).exclude(pk=prog.pk).exists():
                    cand = f"{base_slug[:150]}-{idx}"
                    idx += 1
                prog.slug = cand
            prog.description = (request.POST.get("description") or "").strip()
            cm = (request.POST.get("card_mode") or "equal_max").strip()
            if cm in dict(Program.CARD_MODE_CHOICES):
                prog.card_mode = cm
            try:
                prog.position = int(request.POST.get("position") or 0)
            except (TypeError, ValueError):
                pass
            prog.show_in_nav = request.POST.get("show_in_nav") == "on"
            prog.is_public = request.POST.get("is_public") == "on"
            prog.is_featured = request.POST.get("is_featured") == "on"
            prog.save()
            old_page = PublicPage.objects.filter(slug=f"programm-{old_slug}").first()
            if old_page:
                new_page_slug = f"programm-{prog.slug}"
                if old_slug != prog.slug and not PublicPage.objects.filter(slug=new_page_slug).exclude(pk=old_page.pk).exists():
                    old_page.slug = new_page_slug
                old_page.title = prog.title[:160]
                old_page.save(update_fields=["slug", "title"])
                for blk in old_page.blocks.filter(block_type="annual_program"):
                    bcfg = dict(blk.config_json) if isinstance(blk.config_json, dict) else {}
                    bcfg["program_slug"] = prog.slug
                    blk.config_json = bcfg
                    blk.save(update_fields=["config_json"])
            sc = SiteConfig.get_solo()
            nav_cfg = dict(sc.nav_config_json) if isinstance(sc.nav_config_json, dict) else {}
            nav_items = dict(nav_cfg.get("items") or {}) if isinstance(nav_cfg.get("items"), dict) else {}
            old_nav_key = f"program:{old_slug}"
            new_nav_key = f"program:{prog.slug}"
            if old_nav_key in nav_items or new_nav_key in nav_items:
                entry = dict(nav_items.pop(old_nav_key, None) or nav_items.get(new_nav_key) or {})
                entry["visible"] = prog.show_in_nav
                nav_items[new_nav_key] = entry
                nav_cfg["items"] = nav_items
                sc.nav_config_json = nav_cfg
                sc.save(update_fields=["nav_config_json"])
            messages.success(request, f"Programm „{prog.title}“ aktualisiert.")
        elif action == "delete":
            prog = get_object_or_404(Program, pk=request.POST.get("program_id"))
            title = prog.title
            prog.delete()
            messages.success(request, f"Programm „{title}“ gelöscht.")
    return redirect("site_kit:manage")


@_editor_required
def program_blocks_edit_view(request, pk):
    if not apps.is_installed("cal_kit"):
        raise Http404()
    from cal_kit.models import Program
    prog = get_object_or_404(Program, pk=pk)
    page = _ensure_system_page(f"programm-{prog.slug}")
    return redirect("site_kit:page_edit", pk=page.id)


def _warn_if_reserved_nav_page(request, page: PublicPage):
    return


@_editor_required
def system_page_edit_view(request, slug):
    if slug not in ("news", "termine", "programm"):
        raise Http404()
    page = _ensure_system_page(slug)
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


def _apply_block_bg_image(request, block: PageBlock) -> None:
    if request.FILES.get("bg_image"):
        block.bg_image = request.FILES["bg_image"]
    elif request.POST.get("remove_bg_image") == "1":
        block.bg_image = None
        if isinstance(block.config_json, dict) and block.config_json.get("bg_image_url"):
            cfg = dict(block.config_json)
            cfg["bg_image_url"] = ""
            block.config_json = cfg


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
        if action == "save_program_layout" and page.slug == "programm":
            sc = SiteConfig.get_solo()
            pm = (request.POST.get("program_card_mode") or "equal_max").strip()
            sc.program_card_mode = pm if pm in ("equal_max", "compact_uniform", "auto") else "equal_max"
            sc.save(update_fields=["program_card_mode"])
            messages.success(request, "Darstellung des Programms gespeichert.")
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
                    bg_image=request.FILES.get("bg_image"),
                    video_file=request.FILES.get("video_file"),
                    cta_label=(request.POST.get("cta_label") or "").strip()[:80],
                    cta_url=_sanitize_safe_url(request.POST.get("cta_url") or "")[:255],
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
                    _apply_block_bg_image(request, block)
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
                    block.image_url = ""
                _apply_block_bg_image(request, block)
                if request.FILES.get("video_file"):
                    block.video_file = request.FILES["video_file"]
                elif request.POST.get("remove_video") == "1":
                    block.video_file = None
                if "cta_label" in request.POST:
                    block.cta_label = (request.POST.get("cta_label") or "").strip()[:80]
                if "cta_url" in request.POST:
                    block.cta_url = _sanitize_safe_url(request.POST.get("cta_url") or "")[:255]
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
                if src.bg_image:
                    try:
                        with src.bg_image.open("rb") as fh:
                            dup.bg_image.save((src.bg_image.name or "block-bg.jpg").rsplit("/", 1)[-1], ContentFile(fh.read()), save=True)
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
            page.show_in_nav = False if page.slug in ("news", "termine", "programm") else (request.POST.get("show_in_nav") == "on")
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
    domain_list_categories = []
    if apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Category, Role
            domain_roles = list(Role.objects.order_by("position", "name").values_list("name", flat=True))
            domain_person_categories = list(
                Category.for_purpose("person", "general").values_list("name", flat=True)
            )
            domain_list_categories = list(
                Category.for_purpose("list", "general").values("id", "name")
            )
        except Exception:
            pass
    cal_categories = []
    program_options = []
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarCategory, Program
            cal_categories = list(CalendarCategory.objects.order_by("name").values("id", "name"))
            program_options = list(Program.objects.filter(is_public=True).order_by("position", "title").values("slug", "title"))
        except Exception:
            pass
    form_collections = []
    gallery_collections = []
    public_list_collections = []
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
            public_list_collections = list(
                Collection.objects.filter(is_public=True)
                .exclude(template__content_type="form_inbox")
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
    quick_link_targets = [
        {
            "group": "Standard-Bereiche",
            "items": [
                {"label": "Startseite", "url": reverse("site_kit:home")},
                {"label": "News", "url": reverse("site_kit:news_list")},
                {"label": "Termine", "url": reverse("site_kit:events_list")},
                {"label": "Angebote & Programme", "url": reverse("site_kit:annual_program")},
                {"label": "Dozenten & Team", "url": reverse("site_kit:lecturers_list")},
                {"label": "Anmeldung", "url": reverse("site_kit:event_registration")},
            ],
        },
        {
            "group": "Seiten",
            "items": [
                {"label": pg.title, "url": reverse("site_kit:page_detail", args=[pg.slug])}
                for pg in PublicPage.objects.filter(is_published=True)
                .exclude(slug__in=("news", "termine", "programm"))
                .exclude(slug__startswith="programm-")
            ],
        },
    ]
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import Program
            prog_items = [
                {"label": pr.title, "url": reverse("site_kit:program_detail", args=[pr.slug])}
                for pr in Program.objects.filter(is_public=True)
            ]
            if prog_items:
                quick_link_targets.insert(1, {"group": "Programme", "items": prog_items})
        except Exception:
            pass
    if form_collections:
        quick_link_targets.append({
            "group": "Formulare",
            "items": [
                {"label": fc["title"], "url": reverse("site_kit:public_form", args=[fc["slug"]])}
                for fc in form_collections
            ],
        })
    return render(request, "site_kit/page_editor.html", {
        "page": page,
        "blocks": page.blocks.all(),
        "block_choices": visible_choices,
        "col_choices": PageBlock.COL_SPAN_CHOICES,
        "is_admin": is_admin,
        "domain_roles": domain_roles,
        "domain_person_categories": domain_person_categories,
        "domain_list_categories": domain_list_categories,
        "cal_categories": cal_categories,
        "program_options": program_options,
        "form_collections": form_collections,
        "gallery_collections": gallery_collections,
        "public_list_collections": public_list_collections,
        "public_events": public_events,
        "public_persons": public_persons,
        "quick_link_targets": quick_link_targets,
    })
