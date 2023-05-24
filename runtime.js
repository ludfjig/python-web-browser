console = { log: function(x) { call_python("log", x); } }

document = { querySelectorAll: function(s) {
    var handles = call_python("querySelectorAll", s);
    return handles_to_nodes(handles);
    },
    createElement: function(tag) {
        var handle = call_python("createElement", tag);
        return new Node(handle);
    },
    
}

function handles_to_nodes(handles) {
    return handles.map(function(h) { 
        return new Node(h) 
    });
}

function Node(handle) { 
    this.handle = handle; 
}

Node.prototype.getAttribute = function(attr) {
    return call_python("getAttribute", this.handle, attr);
}

Node.prototype.insertBefore = function(newNode, referenceNode) {
    if (referenceNode === null) {
        call_python("appendChild", this.handle, newNode.handle);
        return newNode;
    }
    call_python("insertBefore", this.handle, newNode.handle, referenceNode.handle);
    return newNode;
}

Node.prototype.appendChild = function(child) {
    call_python("appendChild", this.handle, child.handle);
    return child;
}

LISTENERS = {}

function Event(type) {
    this.type = type
    this.do_default = true;
}

Event.prototype.preventDefault = function() {
    this.do_default = false;
}

Node.prototype.addEventListener = function(type, listener) {
    if (!LISTENERS[this.handle]) LISTENERS[this.handle] = {};
    var dict = LISTENERS[this.handle];
    if (!dict[type]) dict[type] = [];
    var list = dict[type];
    list.push(listener);
}

Object.defineProperty(Node.prototype, 'innerHTML', {
    set: function(s) {
        call_python("innerHTML_set", this.handle, s.toString());
    }
});

Object.defineProperty(Node.prototype, 'children', {
    get: function() {
        return handles_to_nodes(call_python("get_children", this.handle));
    }
});

Node.prototype.dispatchEvent = function(evt) {
    var type = evt.type;
    var handle = this.handle;
    var list = (LISTENERS[handle] && LISTENERS[handle][type]) || [];
    for (var i = 0; i < list.length; i++) {
        list[i].call(this, evt);
    }
    return evt.do_default;
}