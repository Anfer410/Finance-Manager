(function() {
    window.__dragBusId = '__BUS_ID__';
    if (window.__dashboardDragLoaded) return;
    window.__dashboardDragLoaded = true;

    var ROW_H = 180, GAP = 16, THRESH = 5;
    var drag = null, hl = null;

    function getGrid() { return document.querySelector('.dashboard-grid-container'); }

    function calcPos(cx, cy) {
        var g = getGrid(); if (!g) return null;
        var r = g.getBoundingClientRect();
        var cw = (r.width - 3 * GAP) / 4;
        return {
            col: Math.max(1, Math.min(4, Math.floor((cx - r.left) / (cw + GAP)) + 1)),
            row: Math.max(1, Math.floor((cy - r.top) / (ROW_H + GAP)) + 1),
            r: r, cw: cw
        };
    }

    function makeHl() {
        var el = document.createElement('div');
        el.style.cssText = 'position:fixed;pointer-events:none;z-index:9999;' +
            'background:rgba(99,102,241,0.1);border:2px dashed #6366f1;' +
            'border-radius:12px;transition:left 60ms,top 60ms,width 60ms,height 60ms';
        document.body.appendChild(el);
        return el;
    }

    function placeHl(col, row, cs, rs) {
        var g = getGrid(); if (!g || !hl) return;
        var r = g.getBoundingClientRect();
        var cw = (r.width - 3 * GAP) / 4;
        var c = Math.min(col, 5 - cs);
        hl.style.left   = (r.left + (c - 1) * (cw + GAP)) + 'px';
        hl.style.top    = (r.top  + (row - 1) * (ROW_H + GAP)) + 'px';
        hl.style.width  = (cs * cw  + (cs - 1) * GAP) + 'px';
        hl.style.height = (rs * ROW_H + (rs - 1) * GAP) + 'px';
    }

    document.addEventListener('mousedown', function(e) {
        if (e.button !== 0) return;
        var rh = e.target.closest('.dashboard-resize-right');
        var rb = e.target.closest('.dashboard-resize-bottom');
        var mh = e.target.closest('.dashboard-drag-handle');
        if (!rh && !rb && !mh) return;
        if (e.target.closest('button, input, [data-no-drag]')) return;
        var card = (rh || rb || mh).closest('.dashboard-card');
        if (!card) return;
        e.preventDefault();
        var cls = card.className;
        var id = +((cls.match(/\bwid-(\d+)\b/) || [0,0])[1]);
        var cs = +((cls.match(/\bwcs-(\d+)\b/) || [0,1])[1]);
        var rs = +((cls.match(/\bwrs-(\d+)\b/) || [0,1])[1]);
        var co = +((cls.match(/\bwco-(\d+)\b/) || [0,1])[1]);
        var ro = +((cls.match(/\bwro-(\d+)\b/) || [0,1])[1]);
        if (rh)
            drag = {type:'resize-col', id:id, cs:cs, rs:rs, co:co, ro:ro, sx:e.clientX, sy:e.clientY, active:false, val:cs};
        else if (rb)
            drag = {type:'resize-row', id:id, cs:cs, rs:rs, co:co, ro:ro, sx:e.clientX, sy:e.clientY, active:false, val:rs};
        else
            drag = {type:'move', id:id, cs:cs, rs:rs, sx:e.clientX, sy:e.clientY, active:false, col:null, row:null};
    });

    document.addEventListener('mousemove', function(e) {
        if (!drag) return;
        if (!drag.active) {
            if (Math.abs(e.clientX-drag.sx) < THRESH && Math.abs(e.clientY-drag.sy) < THRESH) return;
            drag.active = true;
            hl = makeHl();
            document.body.style.userSelect = 'none';
        }
        var p = calcPos(e.clientX, e.clientY); if (!p) return;
        if (drag.type === 'move') {
            document.body.style.cursor = 'grabbing';
            drag.col = Math.min(p.col, 5 - drag.cs);
            drag.row = p.row;
            placeHl(drag.col, drag.row, drag.cs, drag.rs);
        } else if (drag.type === 'resize-col') {
            document.body.style.cursor = 'ew-resize';
            var new_cs = Math.max(1, Math.min(5 - drag.co, p.col - drag.co + 1));
            drag.val = new_cs;
            placeHl(drag.co, drag.ro, new_cs, drag.rs);
        } else if (drag.type === 'resize-row') {
            document.body.style.cursor = 'ns-resize';
            var new_rs = Math.max(1, p.row - drag.ro + 1);
            drag.val = new_rs;
            placeHl(drag.co, drag.ro, drag.cs, new_rs);
        }
    });

    document.addEventListener('mouseup', function(e) {
        if (!drag) return;
        if (hl) { hl.remove(); hl = null; }
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
        if (drag.active) {
            var bus = document.getElementById(window.__dragBusId);
            if (bus) {
                if (drag.type === 'move' && drag.col !== null) {
                    var evt = new Event('widget-moved');
                    evt.widget_id = drag.id;
                    evt.col = drag.col;
                    evt.row = drag.row;
                    bus.dispatchEvent(evt);
                } else if (drag.type === 'resize-col' || drag.type === 'resize-row') {
                    var evt = new Event('widget-resized');
                    evt.widget_id = drag.id;
                    evt.rtype = drag.type === 'resize-col' ? 'col' : 'row';
                    evt.val = drag.val;
                    bus.dispatchEvent(evt);
                }
            }
        }
        drag = null;
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && drag) {
            if (hl) { hl.remove(); hl = null; }
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            drag = null;
        }
    });
})();
