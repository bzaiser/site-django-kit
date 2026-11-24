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

    elif block.block_type == "downloads" and apps.is_installed("domain_kit"):
        try:
            from domain_kit.models import Document
            data["documents"] = list(Document.objects.filter(is_public=True).order_by("-created_at")[:limit])
        except Exception:
            data["documents"] = []

    elif block.block_type == "stats_bar":
        raw_stats = []
        for line in (block.body or "").splitlines():
            if "|" in line:
                val_part, lbl_part = line.split("|", 1)
                if val_part.strip() and lbl_part.strip():
                    raw_stats.append({"value": val_part.strip()[:40], "label": lbl_part.strip()[:80]})
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
    sys_page = PublicPage.objects.filter(slug="news").first()
    ctx = _public_base_context(request, page=sys_page)
    ctx["extra_blocks"] = [_resolve_block_data(b) for b in sys_page.blocks.filter(is_visible=True)] if sys_page else []
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
    })
