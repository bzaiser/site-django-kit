import re

from django.apps import apps
from django.conf import settings as django_settings


DEFAULT_IMPRINT_TEMPLATE = """Angaben gemäß § 5 Digitale-Dienste-Gesetz (DDG)

{{ vereinsname }}
{{ adresse }}

Vertretungsberechtigter Vorstand (§ 26 BGB):
{{ vorsitz_liste }}

Kontakt:
Telefon: {{ telefon }}
E-Mail: {{ email }}
Internet: {{ webseite }}

Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV:
{{ vorsitz_name }}
{{ adresse }}

Steuerliche Angaben / Hinweise:
{{ steuer_hinweis }}

Verbraucherstreitbeilegung / Universalschlichtungsstelle:
Wir sind nicht bereit oder verpflichtet, an Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle teilzunehmen (§ 36 VSBG).

Haftung für Inhalte und Links:
Als Diensteanbieter sind wir gemäß § 7 Abs. 1 DDG für eigene Inhalte auf diesen Seiten nach den allgemeinen Gesetzen verantwortlich. Für die Inhalte verlinkter externer Webseiten sind stets die jeweiligen Anbieter oder Betreiber verantwortlich; bei Bekanntwerden von Rechtsverletzungen werden derartige Links umgehend entfernt.

Urheberrecht:
Die durch den Verein erstellten Inhalte und Werke auf diesen Seiten unterliegen dem deutschen Urheberrecht. Eine Vervielfältigung, Bearbeitung oder Verbreitung außerhalb der Grenzen des Urheberrechts bedarf der schriftlichen Zustimmung."""


DEFAULT_PRIVACY_TEMPLATE = """Datenschutzerklärung gemäß DSGVO

1. Verantwortlicher
Verantwortlich für die Datenverarbeitung auf dieser Website im Sinne der Datenschutz-Grundverordnung (DSGVO) ist:

{{ vereinsname }}
Vertreten durch: {{ vorsitz_name }}
{{ adresse }}
Telefon: {{ telefon }}
E-Mail: {{ email }}

2. Zugriffsdaten und Server-Logfiles
Beim Besuch unserer Website werden durch den Webserver automatisch Informationen erfasst (IP-Adresse, Datum und Uhrzeit des Zugriffs, aufgerufene Seite, Browsertyp und Betriebssystem). Die Verarbeitung erfolgt zur Sicherstellung eines störungsfreien Betriebs und der Systemsicherheit auf Grundlage von Art. 6 Abs. 1 lit. f DSGVO. Eine Zusammenführung dieser Daten mit anderen Datenquellen findet nicht statt; eine Weitergabe an Dritte zu Werbe- oder Analysezwecken erfolgt nicht.

3. Cookies und lokale Speicherung
Diese Website verwendet ausschließlich technisch notwendige Cookies (z. B. Session- und CSRF-Schutz-Cookies für die Formularsicherheit und den geschützten Mitgliederbereich). Es werden keine Tracking-, Analyse- oder Marketing-Cookies eingesetzt. Rechtsgrundlage ist § 25 Abs. 2 Nr. 2 TDDDG sowie Art. 6 Abs. 1 lit. f DSGVO.

4. Kontaktaufnahme, Online-Formulare, Kursanmeldung und Mitgliedsantrag
Wenn Sie uns über ein Kontakt-, Kursanmelde-, Mitglieds- oder Kündigungsformular Daten übermitteln, werden Ihre Angaben zur Bearbeitung Ihres Anliegens, zur Vertrags- bzw. Kursdurchführung oder zur Begründung/Verwaltung der Mitgliedschaft gespeichert (Art. 6 Abs. 1 lit. b DSGVO sowie bei freiwilligen Angaben Art. 6 Abs. 1 lit. a DSGVO). Eine Weitergabe an unbefugte Dritte erfolgt nicht.

5. Mitgliederbereich / Portal-Login
Sofern Sie über ein persönliches Benutzerkonto für den internen Bereich verfügen, verarbeiten wir Ihre Anmelde- und Profildaten zur Bereitstellung der Vereinsfunktionen (Art. 6 Abs. 1 lit. b und f DSGVO).

6. Eingebettete Videos (2-Klick-Lösung)
Externe Videos (z. B. YouTube oder Vimeo) sind standardmäßig deaktiviert (2-Klick-Verfahren). Erst wenn Sie aktiv auf „Video laden“ klicken, wird eine Verbindung zum jeweiligen Anbieter hergestellt und ggf. Ihre IP-Adresse an diesen übertragen (Art. 6 Abs. 1 lit. a DSGVO).

7. Speicherdauer
Personenbezogene Daten werden gelöscht, sobald der Zweck der Speicherung entfällt und keine gesetzlichen Aufbewahrungspflichten (z. B. handels- und steuerrechtliche Fristen nach AO/HGB, insbesondere 10 Jahre für Zuwendungsbestätigungen und Buchungsbelege) entgegenstehen.

8. Ihre Rechte als betroffene Person
Sie haben jederzeit das Recht auf:
- Auskunft über Ihre gespeicherten personenbezogenen Daten (Art. 15 DSGVO),
- Berichtigung unrichtiger Daten (Art. 16 DSGVO),
- Löschung Ihrer Daten, soweit keine gesetzlichen Aufbewahrungspflichten entgegenstehen (Art. 17 DSGVO),
- Einschränkung der Verarbeitung (Art. 18 DSGVO),
- Datenübertragbarkeit (Art. 20 DSGVO),
- Widerspruch gegen die Verarbeitung (Art. 21 DSGVO) sowie
- Widerruf einer erteilten Einwilligung mit Wirkung für die Zukunft (Art. 7 Abs. 3 DSGVO).

Zudem steht Ihnen ein Beschwerderecht bei der zuständigen Datenschutz-Aufsichtsbehörde zu (Art. 77 DSGVO), z. B. beim Landesbeauftragten für den Datenschutz und die Informationsfreiheit Baden-Württemberg (LfDI BW), Lautenschlagerstraße 20, 70173 Stuttgart."""


def _format_person_address(person) -> str:
    if not person:
        return ""
    try:
        addr = person.addresses.order_by("-is_primary", "id").first()
    except Exception:
        addr = None
    if not addr:
        return ""
    lines = []
    if getattr(addr, "street", ""):
        lines.append(addr.street.strip())
    city_line = " ".join(p for p in (getattr(addr, "postal_code", "").strip(), getattr(addr, "city", "").strip()) if p)
    if city_line:
        lines.append(city_line)
    country = (getattr(addr, "country", "") or "").strip()
    if country and country.lower() not in ("deutschland", "germany", "de"):
        lines.append(country)
    return "\n".join(lines)


def _find_board_persons():
    if not apps.is_installed("domain_kit"):
        return [], None
    try:
        from domain_kit.models import Person
        from django.db.models import Q

        qs = (
            Person.objects.filter(
                Q(roles__name__icontains="vorsitz") | Q(roles__name__icontains="vorstand")
            )
            .prefetch_related("roles", "addresses")
            .distinct()
            .order_by("id")
        )
        people = list(qs)
        if not people:
            return [], None

        def _priority(p):
            rnames = " ".join(r.name.lower() for r in p.roles.all())
            fname = (p.full_name or "").lower()
            if "1." in rnames or "1." in fname or "erste" in rnames:
                return 0
            if "vorsitz" in rnames or "vorsitz" in fname:
                return 1
            return 2

        people.sort(key=_priority)
        return people, people[0]
    except Exception:
        return [], None


def get_dynamic_legal_context(site_cfg, request=None) -> dict[str, str]:
    branding = {}
    try:
        from design_studio.branding import get_branding_context
        branding = get_branding_context("web") or {}
    except Exception:
        branding = {}

    board_list, chair = _find_board_persons()
    vereinsname = (
        getattr(site_cfg, "site_name", "")
        or branding.get("org_name")
        or getattr(django_settings, "DESIGN_STUDIO_APP_NAME", "Verein")
    ).strip()

    vorsitz_name = chair.full_name if chair else (vereinsname or "Vorstand")
    if board_list:
        vorsitz_liste = ", ".join(p.full_name for p in board_list if p.full_name)
    else:
        vorsitz_liste = vorsitz_name

    chair_addr = _format_person_address(chair)
    branding_addr = (branding.get("address") or "").strip()
    adresse = chair_addr or branding_addr or "(Adresse bitte beim Vorstand oder im Branding hinterlegen)"

    email = (
        (getattr(chair, "email", "") or "").strip()
        or (getattr(site_cfg, "contact_email", "") or "").strip()
        or (branding.get("email") or "").strip()
        or "—"
    )
    telefon = (
        (getattr(chair, "phone", "") or "").strip()
        or (getattr(chair, "mobile", "") or "").strip()
        or (branding.get("phone") or "").strip()
        or "—"
    )
    webseite = (branding.get("website") or "").strip()
    if not webseite and request is not None:
        try:
            webseite = request.build_absolute_uri("/")
        except Exception:
            webseite = ""
    webseite = webseite or "—"

    tax_id = (branding.get("tax_id") or "").strip()
    steuer_hinweis = f"Steuernummer / USt-IdNr.: {tax_id}" if tax_id else "Gemeinnütziger Verein (ohne gesonderte USt-IdNr.)"

    return {
        "vereinsname": vereinsname,
        "vorsitz_name": vorsitz_name,
        "vorsitz_liste": vorsitz_liste,
        "ansprechpartner": vorsitz_name,
        "adresse": adresse,
        "email": email,
        "telefon": telefon,
        "webseite": webseite,
        "steuernummer": tax_id or "—",
        "steuer_hinweis": steuer_hinweis,
    }


def render_legal_text(raw_template: str, site_cfg, request=None) -> str:
    ctx = get_dynamic_legal_context(site_cfg, request=request)
    text = raw_template or ""
    for key, val in ctx.items():
        text = re.sub(r"\{\{\s*" + re.escape(key) + r"\s*\}\}", lambda _m, v=val: v, text)
    return text
