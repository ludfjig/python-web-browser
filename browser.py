import socket
import ssl
import sys
import time
import tkinter
import tkinter.font

cache = {}
timers = set()


def request(url, headers={}, depth=0):
    if depth > 10:
        raise Exception("Too many redirects")

    # save original url
    original_url = url

    # format request
    scheme, url = url.split("://", 1)
    assert scheme in ["http", "https", "file"], f"Unknown scheme {scheme}"

    # check cache
    if (scheme in ["http", "https"] and original_url in cache
            and time.time() < cache[original_url][0]):
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

    request_headers = {"Host": host,
                       "Connection": "close",
                       "User-Agent": "TestBrowser"} | headers
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
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return "Text('{}')".format(self.text)


class Tag:
    def __init__(self, tag):
        self.tag = tag

    def __repr__(self):
        return "Tag('{}')".format(self.tag)


def lex(body):
    out = []
    text = ""
    in_tag = False
    for c in body:
        if c == "<":
            in_tag = True
            if text:
                out.append(Text(text))
            text = ""
        elif c == ">":
            in_tag = False
            out.append(Tag(text))
            text = ""
        else:
            text += c
    if not in_tag and text:
        out.append(Text(text))
    return out


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
    def __init__(self, tokens):
        self.display_list = []
        self.cursor_x = HSTEP
        self.cursor_y = VSTEP
        self.weight = "normal"
        self.style = "roman"
        self.size = 16
        self.line = []

        for tok in tokens:
            self.token(tok)
        self.flush()

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

    def token(self, tok):
        if isinstance(tok, Text):
            self.text(tok)
        elif tok.tag == "i":
            self.style = "italic"
        elif tok.tag == "/i":
            self.style = "roman"
        elif tok.tag == "b":
            self.weight = "bold"
        elif tok.tag == "/b":
            self.weight = "normal"
        elif tok.tag == "small":
            self.size -= 2
        elif tok.tag == "/small":
            self.size += 2
        elif tok.tag == "big":
            self.size += 4
        elif tok.tag == "/big":
            self.size -= 4
        elif tok.tag == "br":
            self.flush()
        elif tok.tag == "/p":
            self.flush()
            self.cursor_y += VSTEP

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
            if y > self.scroll+HEIGHT:
                continue
            if y + VSTEP < self.scroll:
                continue
            self.canvas.create_text(x, y-self.scroll, text=c, font=f, anchor="nw")

    def load(self, url):
        headers, body = request(url)
        tokens = lex(body)
        self.display_list = Layout(tokens).display_list
        self.draw()


if __name__ == "__main__":
    if len(sys.argv) == 1:
        Browser().load(DEFAULT_FILE_URL)
    else:
        Browser().load(sys.argv[1])
    tkinter.mainloop()
