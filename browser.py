import socket
import ssl
import sys
import time
import tkinter
import tkinter.font
import shlex

cache = {}
timers = set()
BLOCK_ELEMENTS = [
    "html", "body", "article", "section", "nav", "aside",
    "h1", "h2", "h3", "h4", "h5", "h6", "hgroup", "header",
    "footer", "address", "p", "hr", "pre", "blockquote",
    "ol", "ul", "menu", "li", "dl", "dt", "dd", "figure",
    "figcaption", "main", "div", "table", "form", "fieldset",
    "legend", "details", "summary"
]


def p(m):
    print(m, file=sys.stderr)


def request(url, headers={}, depth=0):
    if depth > 10:
        raise Exception("Too many redirects")

    # save original url
    original_url = url

    # format request
    scheme, url = url.split("://", 1)
    assert scheme in ["http", "https", "file"], f"Unknown scheme {scheme}"

    # check cache
    if (
        scheme in ["http", "https"]
        and original_url in cache
        and time.time() < cache[original_url][0]
    ):
        return cache[original_url][1:]

    if scheme == "file":
        f = open(url)
        body = f.read()
        return {}, body

    if "/" in url:
        host, path = url.split("/", 1)
        path = "/" + path
    else:
        host = url
        path = "/"
    port = 80 if scheme == "http" else 443

    if ":" in host:
        host, port = host.split(":", 1)
        port = int(port)

    # create socket
    s = socket.socket()
    if scheme == "https":
        ctx = ssl.create_default_context()
        s = ctx.wrap_socket(s, server_hostname=host)
    s.connect((host, port))

    request_headers = {
        "Host": host,
        "Connection": "close",
        "User-Agent": "TestBrowser",
    } | headers
    request_headers = {k.lower(): v for k, v in request_headers.items()}

    # construct request HTTP string
    request_string = f"GET {path} HTTP/1.1\r\n"
    for header, value in request_headers.items():
        request_string += f"{header}: {value}\r\n"
    request_string += "\r\n"

    # send request
    s.send(request_string.encode("utf8"))

    # read response
    response = s.makefile("r", encoding="utf8", newline="\r\n")
    statusline = response.readline()
    version, status, explanation = statusline.split(" ", 2)
    status = int(status)
    assert status == 200 or 300 <= status <= 399, "{}: {}".format(
        status, explanation)

    # parse headers into dict
    response_headers = {}
    while True:
        line = response.readline()
        if line == "\r\n":
            break
        header, value = line.split(":", 1)
        response_headers[header.lower()] = value.strip()

    assert "transfer-encoding" not in response_headers
    assert "content-encoding" not in response_headers

    # follow redirects
    if 300 <= status <= 399:
        location = response_headers["location"]
        if location.startswith("/"):
            location = scheme + "://" + host + location
        return request(location, depth=depth + 1)

    # read body
    body = response.read()

    # cache response
    if "cache-control" in response_headers and status == 200:
        cache_control = response_headers["cache-control"]
        if cache_control != "no-store":
            assert cache_control.startswith(
                "max-age="), "Unknown cache-control"
            age = int(cache_control.split("=", 1)[1])
            cache[original_url] = (time.time() + age, response_headers, body)

    s.close()

    return response_headers, body


def layout_mode(node):
    if isinstance(node, Text):
        return "inline"
    elif node.children:
        for child in node.children:
            if isinstance(child, Text):
                continue
            if child.tag in BLOCK_ELEMENTS:
                return "block"
        return "inline"
    else:
        return "block"


class Text:
    def __init__(self, text, parent):
        self.text = text
        self.children = []
        self.parent = parent

    def __repr__(self):
        return repr(self.text)


class Element:
    def __init__(self, tag, attributes, parent):
        self.tag = tag
        self.attributes = attributes
        self.children = []
        self.parent = parent

    def __repr__(self):
        attrs = [" " + k + '="' + v + '"' for k, v in self.attributes.items()]
        return "<" + self.tag + "".join(attrs) + ">"


class HTMLParser:
    def __init__(self, body):
        self.body = body
        self.unfinished = []
        self.SELF_CLOSING_TAGS = [
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        ]
        self.HEAD_TAGS = [
            "base", "basefont", "bgsound", "noscript",
            "link", "meta", "title", "style", "script",
        ]

    def parse(self):
        text = ""
        in_tag = False
        for c in self.body:
            if c == "<":
                in_tag = True
                if text:
                    self.add_text(text)
                text = ""
            elif c == ">":
                in_tag = False
                self.add_tag(text)
                text = ""
            else:
                text += c
        if not in_tag and text:
            self.add_text(text)
        return self.finish()

    def get_attributes(self, text):
        parts = text.split()
        tag = parts[0].lower()
        attributes = {}
        for attrpair in parts[1:]:
            if "=" in attrpair:
                key, value = attrpair.split("=", 1)
                if len(value) > 2 and value[0] in ["'", "\""]:
                    value = value[1:-1]
                attributes[key.lower()] = value
            else:
                attributes[attrpair.lower()] = ""
        return tag, attributes

    def add_text(self, text):
        if text.isspace():
            return
        self.implicit_tags(None)
        parent = self.unfinished[-1]
        node = Text(text, parent)
        parent.children.append(node)

    def add_tag(self, tag):
        tag, attributes = self.get_attributes(tag)

        if tag.startswith("!"):
            return
        self.implicit_tags(tag)
        if tag.startswith("/"):
            if len(self.unfinished) == 1:
                return
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        elif tag in self.SELF_CLOSING_TAGS:
            parent = self.unfinished[-1]
            node = Element(tag, attributes, parent)
            parent.children.append(node)
        else:
            found = False
            parent = self.unfinished[-1] if self.unfinished else None

            # check if there is unclosed paragraph tag
            for unfinished in self.unfinished:
                if isinstance(parent, Element) and unfinished.tag == "p":
                    found = True
                    break

            # close all unclosed tags then reopen them
            if found and tag == "p":
                closed_tags = []
                while True:
                    node = self.unfinished.pop()
                    parent2 = self.unfinished[-1]
                    parent2.children.append(node)
                    if isinstance(node, Element) and node.tag != "p":
                        closed_tags.append(node)
                    if isinstance(node, Element) and node.tag == "p":
                        break

            node = Element(tag, attributes, parent)
            self.unfinished.append(node)
            if found and tag == "p":
                for closed in closed_tags:
                    new = Element(closed.tag, closed.attributes, closed.parent)
                    self.unfinished.append(new)

    def implicit_tags(self, tag):
        while True:
            open_tags = [node.tag for node in self.unfinished]
            if open_tags == [] and tag != "html":
                self.add_tag("html")
            elif open_tags == ["html"] and tag not in ["head", "body", "/html"]:
                if tag in self.HEAD_TAGS:
                    self.add_tag("head")
                else:
                    self.add_tag("body")
            elif (
                open_tags == ["html", "head"] and tag not in [
                    "/head"] + self.HEAD_TAGS
            ):
                self.add_tag("/head")
            else:
                break

    def finish(self):
        if len(self.unfinished) == 0:
            self.add_tag("html")
        while len(self.unfinished) > 1:
            node = self.unfinished.pop()
            parent = self.unfinished[-1]
            parent.children.append(node)
        return self.unfinished.pop()


def print_tree(node, indent=0):
    print(" " * indent, node)
    for child in node.children:
        print_tree(child, indent + 2)


def show(body):
    in_angle = False
    for c in body:
        if c == "<":
            in_angle = True
        elif c == ">":
            in_angle = False
        elif not in_angle:
            print(c, end="")


WIDTH, HEIGHT = 800, 600
HSTEP, VSTEP = 13, 18
DEFAULT_FILE_URL = "file://browser.py"
SCROLL_STEP = 100
FONTS = {}


def get_font(size, weight, slant):
    key = (size, weight, slant)
    if key not in FONTS:
        font = tkinter.font.Font(size=size, weight=weight, slant=slant)
        FONTS[key] = font
    return FONTS[key]


class DocumentLayout:
    def __init__(self, node):
        self.node = node
        self.parent = None
        self.children = []

    def layout(self):
        child = BlockLayout(self.node, self, None)
        self.children.append(child)
        child.layout()

# mypy typechecker for python


class BlockLayout:
    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = None

    def layout(self):
        previous = None
        for child in self.node.children:
            if layout_mode(child) == "inline":
                next = InlineLayout(child, self, previous)
            else:
                next = BlockLayout(child, self, previous)
            self.children.append(next)
            previous = next

        self.width = self.parent.width
        self.x = self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        for child in self.children:
            child.layout()

        self.height = sum([child.height for child in self.children])

    def paint(self, display_list):
        for child in self.children:
            child.paint(display_list)

    def __repr__(self):
        return "BlockLayout(x={}, y={}, width={}, height={})".format(
            self.x, self.y, self.width, self.height)


class InlineLayout:
    def __init__(self, node, parent, previous):
        self.node = node
        self.parent = parent
        self.previous = previous
        self.children = []
        self.x = None
        self.y = None
        self.width = None
        self.height = None
        self.display_list = None

    def layout(self):
        self.width = self.parent.width
        self.x = self.parent.x

        if self.previous:
            self.y = self.previous.y + self.previous.height
        else:
            self.y = self.parent.y

        self.display_list = []
        self.weight = "normal"
        self.style = "roman"
        self.size = 16

        self.cursor_x = self.x
        self.cursor_y = self.y
        self.line = []
        self.recurse(self.node)
        self.flush()

        self.height = self.cursor_y - self.y

    def recurse(self, node):
        if isinstance(node, Text):
            self.text(node)
        else:
            self.open_tag(node.tag)
            for child in node.children:
                self.recurse(child)
            self.close_tag(node.tag)

    def open_tag(self, tag):
        if tag == "i":
            self.style = "italic"
        elif tag == "b":
            self.weight = "bold"
        elif tag == "small":
            self.size -= 2
        elif tag == "big":
            self.size += 4
        elif tag == "br":
            self.flush()

    def close_tag(self, tag):
        if tag == "i":
            self.style = "roman"
        elif tag == "b":
            self.weight = "normal"
        elif tag == "small":
            self.size += 2
        elif tag == "big":
            self.size -= 4
        elif tag == "p":
            self.flush()
            self.cursor_y += VSTEP

    def text(self, node):
        font = get_font(self.size, self.weight, self.style)
        for word in node.text.split():
            w = font.measure(word)
            if self.cursor_x + w > self.width - HSTEP:
                self.flush()
            self.line.append((self.cursor_x, word, font))
            self.cursor_x += w + font.measure(" ")

    def flush(self):
        if not self.line:
            return
        metrics = [font.metrics() for x, word, font in self.line]
        max_ascent = max([metric["ascent"] for metric in metrics])
        baseline = self.cursor_y + 1.25 * max_ascent
        for x, word, font in self.line:
            y = baseline - font.metrics("ascent")
            self.display_list.append((x, y, word, font))
        self.cursor_x = self.x
        self.line = []
        max_descent = max([metric["descent"] for metric in metrics])
        self.cursor_y = baseline + 1.25 * max_descent

    def paint(self, display_list):
        if isinstance(self.node, Element) and self.node.tag == "pre":
            x2, y2 = self.x + self.width, self.y + self.height
            rect = DrawRect(self.x, self.y, x2, y2, "gray")
            display_list.append(rect)
        for x, y, word, font in self.display_list:
            display_list.append(DrawText(x, y, word, font))

    def __repr__(self):
        return "InlineLayout(x={}, y={}, width={}, height={})".format(
            self.x, self.y, self.width, self.height)


class DocumentLayout:
    def __init__(self, node):
        self.node = node
        self.parent = None
        self.previous = None
        self.children = []

    def layout(self):
        child = BlockLayout(self.node, self, None)
        self.children.append(child)

        self.width = WIDTH - 2*HSTEP
        self.x = HSTEP
        self.y = VSTEP
        child.layout()
        self.height = child.height + 2*VSTEP

    def paint(self, display_list):
        self.children[0].paint(display_list)

    def __repr__(self):
        return "DocumentLayout()"


class DrawText:
    def __init__(self, x1, y1, text, font):
        self.top = y1
        self.left = x1
        self.text = text
        self.font = font

        self.bottom = y1 + font.metrics("linespace")

    def execute(self, scroll, canvas):
        canvas.create_text(
            self.left, self.top - scroll,
            text=self.text,
            font=self.font,
            anchor='nw',
        )

    def __repr__(self):
        return "DrawText(top={} left={} bottom={} text={} font={})".format(
            self.top, self.left, self.bottom, self.text, self.font)


class DrawRect:
    def __init__(self, x1, y1, x2, y2, color):
        self.top = y1
        self.left = x1
        self.bottom = y2
        self.right = x2
        self.color = color

    def execute(self, scroll, canvas):
        canvas.create_rectangle(
            self.left, self.top - scroll,
            self.right, self.bottom - scroll,
            width=0,
            fill=self.color,
        )

    def __repr__(self):
        return "DrawRect(top={} left={} bottom={} right={} color={})".format(
            self.top, self.left, self.bottom, self.right, self.color)


class Browser:
    def __init__(self):
        self.window = tkinter.Tk()
        self.canvas = tkinter.Canvas(
            self.window,
            width=WIDTH,
            height=HEIGHT
        )
        self.canvas.pack()

        self.scroll = 0
        self.window.bind("<Down>", self.scrolldown)
        self.display_list = []

    def scrolldown(self, event):
        max_y = self.document.height - HEIGHT
        self.scroll = min(self.scroll + SCROLL_STEP, max_y)
        self.draw()

    def draw(self):
        self.canvas.delete("all")
        for cmd in self.display_list:
            if cmd.top > self.scroll + HEIGHT:
                continue
            if cmd.bottom < self.scroll:
                continue
            cmd.execute(self.scroll, self.canvas)

    def load(self, url):
        headers, body = request(url)
        self.nodes = HTMLParser(body).parse()
        self.document = DocumentLayout(self.nodes)
        self.document.layout()
        self.display_list = []
        self.document.paint(self.display_list)
        self.draw()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        Browser().load(DEFAULT_FILE_URL)
    else:
        Browser().load(sys.argv[1])
    tkinter.mainloop()
