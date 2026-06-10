import html
import re
from dataclasses import dataclass, field


@dataclass
class SelectOption:
    value: str
    label: str
    attrs: dict[str, str] = field(default_factory=dict)


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_select_options(page_html: str, select_id: str) -> list[SelectOption]:
    select_match = re.search(
        rf"<select[^>]*id=['\"]{re.escape(select_id)}['\"][^>]*>(.*?)</select>",
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        return []

    block = select_match.group(1)
    options: list[SelectOption] = []
    for option_match in re.finditer(
        r"<option(?P<attrs>[^>]*)value\s*=\s*['\"](?P<value>[^'\"]*)['\"](?P<attrs2>[^>]*)>(?P<label>.*?)</option>",
        block,
        re.IGNORECASE | re.DOTALL,
    ):
        attrs_text = f"{option_match.group('attrs')} {option_match.group('attrs2')}"
        attrs = {
            key: value
            for key, value in re.findall(r"([a-zA-Z0-9_-]+)\s*=\s*['\"]([^'\"]*)['\"]", attrs_text)
        }
        options.append(
            SelectOption(
                value=option_match.group("value").strip(),
                label=clean_text(option_match.group("label")),
                attrs=attrs,
            )
        )
    return options
