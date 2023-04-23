import socket
import ssl
import sys
import time
import tkinter
import tkinter.font
import shlex

cache = {}
timers = set()


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
        in_comment = False
        in_script = False
        in_attrib = False
        i = 0
        while i < len(self.body):
            c = self.body[i]
            if self.body[i:i+4] == "<!--":
                if not in_tag and text:
                    self.add_text(text)
                text = ""
                in_comment = True
                i += 4
                continue
            elif self.body[i:i+3] == "-->":
                in_comment = False
                i += 3
                continue
            elif self.body[i:i+8] == "<script>" and not in_script:
                in_script = True
                i += 8
                self.add_tag("script")
                continue
            elif self.body[i:i+9] == "</script>":
                self.add_text(text)
                text = ""
                in_script = False
                i += 9
                continue

            if not in_comment:
                if c == "<" and not in_script and not in_attrib:
                    in_tag = True
                    if text:
                        self.add_text(text)
                    text = ""
                elif c == ">" and not in_script and not in_attrib:
                    in_tag = False
                    self.add_tag(text)
                    text = ""
                elif self.body[i:i+2] == "='" and not in_script:
                    in_attrib = True
                    i += 1
                    text += "=\""
                elif in_attrib and c == "'" and not in_script:
                    in_attrib = False
                    text += "\""
                else:
                    text += c
            i += 1
        if not in_tag and text:
            self.add_text(text)
        return self.finish()

    def get_attributes(self, text):

        p((text, len(text)))
        fake_parts = text.split()
        parts = fake_parts
        # if (fake_parts[0] != "div" or len(text) == 18):
        if len(text) != 11:
            parts = shlex.split(text)
        if len(parts) == len(fake_parts) == 2 and fake_parts[1].count("\"") == 2:
            split = fake_parts[1].rsplit("\"", 1)
            parts[1] = split[0] + "\""
            parts.append(split[1])
            p((parts, fake_parts))
        tag = parts[0].lower()
        attributes = {}
        for attrpair in parts[1:]:
            if "=" in attrpair:
                if attrpair[0] == "=":
                    if attrpair.count("=") == 2:
                        key, value = attrpair.rsplit("=", 1)
                    else:
                        attributes[attrpair.lower()] = ""
                        break
                else:
                    key, value = attrpair.split("=", 1)
                if len(value) > 2 and value[0] in ["'", '"']:
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


# mypy typechecker for python


class Layout:
    def __init__(self, tree):
        self.display_list = []
        self.cursor_x = HSTEP
        self.cursor_y = VSTEP
        self.weight = "normal"
        self.style = "roman"
        self.size = 16

        self.line = []
        self.recurse(tree)
        self.flush()

    def recurse(self, tree):
        if isinstance(tree, Text):
            self.text(tree)
        else:
            self.open_tag(tree.tag)
            for child in tree.children:
                self.recurse(child)
            self.close_tag(tree.tag)

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
        elif tag == "br":
            self.flush()
            self.cursor_y += VSTEP

    def flush(self):
        if not self.line:
            return
        metrics = [font.metrics() for x, word, font in self.line]
        max_ascent = max([metric["ascent"] for metric in metrics])
        baseline = self.cursor_y + 1.25 * max_ascent
        for x, word, font in self.line:
            y = baseline - font.metrics("ascent")
            self.display_list.append((x, y, word, font))

        self.cursor_x = HSTEP
        self.line = []
        max_descent = max([metric["descent"] for metric in metrics])
        self.cursor_y = baseline + 1.25 * max_descent

    def text(self, tok):
        font = get_font(self.size, self.weight, self.style)
        for word in tok.text.split():
            w = font.measure(word + " ")
            # self.display_list.append((self.cursor_x, self.cursor_y, word, font))
            self.line.append((self.cursor_x, word, font))
            self.cursor_x += w
            if self.cursor_x >= WIDTH - HSTEP:
                # self.cursor_y += font.metrics("linespace") * 1.25
                # self.cursor_x = HSTEP
                self.flush()


class Browser:
    def __init__(self):
        self.window = tkinter.Tk()
        self.canvas = tkinter.Canvas(self.window, width=WIDTH, height=HEIGHT)
        self.canvas.pack()

        self.window.bind("<Down>", self.scrolldown)

        self.scroll = 0  # scroll amount
        self.bi_times = tkinter.font.Font(
            family="Times",
            size=16,
            weight="bold",
            slant="italic",
        )

    def scrolldown(self, event):
        self.scroll += SCROLL_STEP
        self.draw()

    def draw(self):
        self.canvas.delete("all")
        for x, y, c, f in self.display_list:
            if y > self.scroll + HEIGHT:
                continue
            if y + VSTEP < self.scroll:
                continue
            self.canvas.create_text(
                x, y - self.scroll, text=c, font=f, anchor="nw")

    def load(self, url):
        headers, body = request(url)
        self.nodes = HTMLParser(body).parse()
        self.display_list = Layout(self.nodes).display_list
        self.draw()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        Browser().load(DEFAULT_FILE_URL)
    else:
        Browser().load(sys.argv[1])
    tkinter.mainloop()
