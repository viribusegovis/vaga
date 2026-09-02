"""Read and write searches.yaml from the UI without destroying it.

`searches.yaml` is hand-editable and heavily commented — those comments explain
why every weight is what it is, so a round-trip through yaml.safe_dump would be
a real loss. Nothing here reformats the file:

  * `set_scalar` rewrites exactly one value on exactly one line.
  * `replace_block` regenerates one top-level-ish block (the searches list, the
    commute table) and leaves every other line untouched. Comments *above* a
    block survive; comments *inside* a regenerated block do not, which is why
    only data-shaped blocks are ever regenerated.

Anything not covered is edited through the raw YAML editor in the UI, where the
user is editing the real text and nothing is lost.
"""

import re

import yaml

import store

PATH = store.CONFIG

# Keys can contain spaces and punctuation ("Castelo Branco:", "1:"), but a list
# item ("- id: x") must not be mistaken for one.
KEY_RE = re.compile(r"^(\s*)([\w .()/&'-]+):(\s*)(.*)$")


def raw():
    return PATH.read_text(encoding="utf-8")


def load():
    return yaml.safe_load(raw())


def save_raw(text):
    """Validate then write. Returns None on success, or the error message."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return "YAML error: %s" % e
    if not isinstance(parsed, dict):
        return "Top level must be a mapping."
    for required in ("searches", "reject", "scoring", "fetch"):
        if required not in parsed:
            return "Missing the %r section." % required
    PATH.write_text(text, encoding="utf-8")
    return None


def fmt(value):
    """Render a Python value the way this file writes them."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[%s]" % ", ".join(str(v) for v in value)
    text = str(value)
    return text if re.fullmatch(r"[\w.:/-]+", text) else '"%s"' % text


def walk(text):
    """Yield (line_index, indent, path, value_text) for every key line."""
    stack = []
    for i, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        m = KEY_RE.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, m.group(2)))
        yield i, indent, [k for _, k in stack], m.group(4)


def set_scalar(text, path, value):
    """Rewrite one value in place, keeping its line's comment and indentation."""
    lines = text.split("\n")
    for i, indent, found, current in walk(text):
        if found != list(path):
            continue
        trailing = ""
        # Keep an end-of-line comment if the value itself has no '#' in it.
        m = re.search(r"\s+#.*$", current)
        if m and not current.strip().startswith('"'):
            trailing = m.group(0)
        lines[i] = "%s%s: %s%s" % (" " * indent, path[-1], fmt(value), trailing)
        return "\n".join(lines)
    raise KeyError(":".join(path))


def replace_block(text, path, body):
    """Swap a block's contents, keeping the key line and everything around it.

    `body` is the already-indented YAML for the block's children.
    """
    lines = text.split("\n")
    for i, indent, found, _ in walk(text):
        if found != list(path):
            continue
        # The block runs until the next line indented at or below the key.
        end = len(lines)
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= indent:
                end = j
                break
        return "\n".join(lines[:i + 1] + body.split("\n") + lines[end:])
    raise KeyError(":".join(path))


def dump_block(data, indent):
    """yaml.safe_dump one value, re-indented to sit under its key."""
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False).rstrip("\n")
    pad = " " * indent
    return "\n".join(pad + line for line in text.split("\n"))


def update(changes):
    """Apply {(path tuple): value} to the file. Returns an error or None."""
    text = raw()
    for path, value in changes.items():
        try:
            text = set_scalar(text, path, value)
        except KeyError:
            return "No setting called %r in searches.yaml." % ":".join(path)
    return save_raw(text)


def update_block(path, data, indent):
    text = raw()
    try:
        text = replace_block(text, path, dump_block(data, indent))
    except KeyError:
        return "No block called %r in searches.yaml." % ":".join(path)
    return save_raw(text)
