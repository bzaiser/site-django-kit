import json
from xml.sax.saxutils import escape

from django.apps import apps
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import PageBlock, PublicPage, SiteConfig


def _public_base_context(request, page: PublicPage | None = None) -> dict:
    cfg = SiteConfig.get_solo()
    nav_pages = list(PublicPage.objects.filter(is_published=True, show_in_nav=True))
    nav_keys = {p.slug.lower() for p in nav_pages if not p.is_home} | {
        p.title.strip().lower() for p in nav_pages if not p.is_home
    }
    ctx = {
        "site_cfg": cfg,
        "public_nav_pages": nav_pages,
        "show_default_news_nav": "news" not in nav_keys,
        "show_default_events_nav": "termine" not in nav_keys,
        "page": page,
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
            data["people"] = list(Person.objects.filter(is_public=True).prefetch_related("roles")[:limit])
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
    if apps.is_installed("cal_kit"):
        try:
            from cal_kit.models import CalendarEvent
            events = list(CalendarEvent.objects.filter(is_public=True, end_at__gte=timezone.now()).order_by("start_at")[:50])
        except Exception:
            events = []
    ctx["events"] = events
    return render(request, "site_kit/events_list.html", ctx)


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


@staff_member_required
def manage_view(request):
    cfg = SiteConfig.get_solo()
    if request.method == "POST":
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
    return render(request, "site_kit/manage.html", {"site_cfg": cfg, "pages": pages})


@staff_member_required
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
            return redirect("site_kit:page_edit", pk=p.id)
    return redirect("site_kit:manage")


@staff_member_required
def page_delete_view(request, pk):
    page = get_object_or_404(PublicPage, pk=pk)
    if request.method == "POST":
        title = page.title
        page.delete()
        messages.success(request, f"Seite „{title}“ gelöscht.")
    return redirect("site_kit:manage")


@staff_member_required
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
        return redirect("site_kit:page_edit", pk=page.id)

    return render(request, "site_kit/page_editor.html", {
        "page": page,
        "blocks": page.blocks.all(),
        "block_choices": PageBlock.BLOCK_TYPE_CHOICES,
    })
