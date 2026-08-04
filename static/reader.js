/* 阅读页交互：点词查词、点句解析、生词本。
 * 兼容老旧 WebView：不用 closest / classList，纯 ES5。 */
(function () {
  "use strict";

  var CFG = null;
  var panel = null;
  var pbody = null;
  var curWord = null;
  var curSent = null;
  var hlEl = null;

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function nl2br(s) { return esc(s).replace(/\n/g, "<br>"); }

  function hasClass(el, cls) {
    return (" " + (el.className || "") + " ").indexOf(" " + cls + " ") !== -1;
  }

  function addClass(el, cls) {
    if (!hasClass(el, cls)) {
      el.className = (el.className ? el.className + " " : "") + cls;
    }
  }

  function removeClass(el, cls) {
    var re = new RegExp("(^|\\s)" + cls + "(\\s|$)", "g");
    el.className = (el.className || "").replace(re, " ").replace(/^\s+|\s+$/g, "");
  }

  function up(el, cls) {
    while (el && el.nodeType === 1) {
      if (hasClass(el, cls)) { return el; }
      el = el.parentNode;
    }
    return null;
  }

  function post(url, data, done) {
    var xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      var r = null;
      try { r = JSON.parse(xhr.responseText); } catch (e) { r = null; }
      done(r, xhr.status);
    };
    xhr.send(JSON.stringify(data));
  }

  function setPanel(html) {
    removeClass(panel, "off");
    addClass(document.body, "panel-open");
    panel.scrollTop = 0;
    pbody.innerHTML = html;
  }

  function closePanel() {
    addClass(panel, "off");
    removeClass(document.body, "panel-open");
    highlight(null);
  }

  function highlight(el) {
    if (hlEl) { removeClass(hlEl, "hl"); }
    hlEl = el;
    if (el) { addClass(el, "hl"); }
  }

  function wordHTML(r) {
    var h = "";
    if (!r.found) {
      h += '<div class="whead"><b>' + esc(r.word) + '</b></div>';
      h += '<div class="pos">词典未收录此词</div>';
      h += '<div class="tran">可用 AI 根据语境解释句中意。</div>';
      h += '<button class="btn" id="btn-ins">AI 解释句中意</button>';
      return h;
    }
    h += '<div class="whead"><b>' + esc(r.word) + '</b>';
    if (r.phonetic) { h += '<span class="ph">' + esc(r.phonetic) + '</span>'; }
    if (r.levels) { h += '<span class="lvl">' + esc(r.levels) + '</span>'; }
    h += '</div>';
    if (r.pos) { h += '<div class="pos">' + esc(r.pos) + '</div>'; }
    if (r.translation) { h += '<div class="tran">' + nl2br(r.translation) + '</div>'; }
    if (r.exchange) { h += '<div class="exch">变形：' + esc(r.exchange) + '</div>'; }
    if (r.from) { h += '<div class="from">词形变化自 "' + esc(r.from) + '"</div>'; }
    if (r.insent) {
      h += '<div class="ins"><b>句中意：</b>' + esc(r.insent) + '</div>';
    } else {
      h += '<button class="btn" id="btn-ins">AI 句中意</button>';
    }
    h += r.saved
      ? '<button class="btn" id="btn-rm">移出生词本</button>'
      : '<button class="btn" id="btn-add">加入生词本</button>';
    return h;
  }

  function sentHTML(r) {
    var h = '<div class="whead"><b>句子解析</b></div>';
    h += '<div class="quote">' + nl2br(r.s) + '</div>';
    if (r.translation) {
      h += '<div class="tran"><b>译文：</b>' + esc(r.translation) + '</div>';
    }
    if (r.grammar && r.grammar.length) {
      h += '<div class="gtitle">语法要点</div><ul>';
      for (var i = 0; i < r.grammar.length; i++) {
        h += '<li>' + esc(r.grammar[i]) + '</li>';
      }
      h += '</ul>';
    }
    if (r.words && r.words.length) {
      h += '<div class="gtitle">重点词</div><ul>';
      for (var j = 0; j < r.words.length; j++) {
        var w = r.words[j];
        h += '<li><b>' + esc(w.w) + '</b>';
        if (w.pos) { h += ' <i>' + esc(w.pos) + '</i>'; }
        h += '：' + esc(w.m) + '</li>';
      }
      h += '</ul>';
    }
    if (r.raw) { h += '<div class="tran">' + nl2br(r.raw) + '</div>'; }
    return h;
  }

  function openWord(el) {
    highlight(el);
    curWord = el.textContent;
    var sEl = up(el, "s");
    curSent = sEl ? sEl.textContent : "";
    setPanel("<div>查词中…</div>");
    post("/api/word", { book: CFG.book, page: CFG.page, w: curWord, s: curSent }, function (r) {
      if (!r) { setPanel("<div>网络错误</div>"); return; }
      setPanel(wordHTML(r));
    });
  }

  function openSent(el) {
    highlight(el);
    curSent = el.textContent;
    setPanel("<div>AI 解析中…</div>");
    post("/api/sentence", { s: curSent, book: CFG.book, page: CFG.page, fs: CFG.fs }, function (r, st) {
      if (!r) { setPanel("<div>网络错误</div>"); return; }
      if (st === 503) { setPanel("<div>" + esc(r.error || "AI 未配置") + "</div>"); return; }
      setPanel(sentHTML(r));
    });
  }

  function genInsent() {
    setPanel("<div>AI 生成中…</div>");
    post("/api/insent", { w: curWord, s: curSent }, function (r, st) {
      if (!r) { setPanel("<div>网络错误</div>"); return; }
      if (st === 503 || st === 502) { setPanel("<div>" + esc(r.error || "AI 调用失败") + "</div>"); return; }
      setPanel(
        '<div class="whead"><b>' + esc(curWord) + '</b></div>' +
        '<div class="ins"><b>句中意：</b>' + esc(r.meaning || r.raw || "") + '</div>' +
        (r.pos ? '<div class="pos">' + esc(r.pos) + '</div>' : '') +
        '<button class="btn" id="btn-reword">返回词典释义</button>'
      );
    });
  }

  function backWord() {
    post("/api/word", { book: CFG.book, page: CFG.page, w: curWord, s: curSent }, function (r) {
      setPanel(r ? wordHTML(r) : "<div>网络错误</div>");
    });
  }

  function vocabAdd() {
    post("/api/vocab/add", { book: CFG.book, word: curWord }, function () { backWord(); });
  }

  function vocabRemove() {
    post("/api/vocab/remove", { book: CFG.book, word: curWord }, function () { backWord(); });
  }

  function targetOf(e) {
    var t = e.target || e.srcElement;
    while (t && t.nodeType !== 1) { t = t.parentNode; }
    return t;
  }

  var swipeTime = 0;

  function initTouch() {
    var startX = 0, startY = 0, startEl = null;
    var c = document.getElementById("content");

    c.addEventListener("touchstart", function (e) {
      var t = (e.touches && e.touches.length === 1) ? e.touches[0] : null;
      if (!t) { startEl = null; return; }
      startX = t.clientX;
      startY = t.clientY;
      startEl = targetOf(e);
    }, false);

    c.addEventListener("touchmove", function (e) {
      if (!startEl) { return; }
      var t = (e.touches && e.touches.length === 1) ? e.touches[0] : null;
      if (!t) { return; }
      var dx = t.clientX - startX;
      var dy = t.clientY - startY;
      if (Math.abs(dy) > 12) {
        startEl = null;
      } else if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) {
        e.preventDefault();
      }
    }, false);

    c.addEventListener("touchend", function (e) {
      if (!startEl) { return; }
      var t = (e.changedTouches && e.changedTouches.length === 1)
        ? e.changedTouches[0] : null;
      var el = startEl;
      startEl = null;
      if (!t) { return; }
      var dx = t.clientX - startX;
      var dy = t.clientY - startY;
      if ((dx > 24 || dx < -24) && Math.abs(dy) < 16) {
        var s = up(el, "s");
        if (s) {
          swipeTime = new Date().getTime();
          openSent(s);
        }
      }
    }, false);

    c.addEventListener("touchcancel", function () { startEl = null; }, false);
  }

  function init(cfg) {
    CFG = cfg;
    panel = document.getElementById("panel");
    pbody = document.getElementById("pbody");

    document.getElementById("content").addEventListener("click", function (e) {
      if (new Date().getTime() - swipeTime < 400) { return; }
      var t = targetOf(e);
      var w = t ? up(t, "w") : null;
      if (w) { openWord(w); return; }
      var s = t ? up(t, "s") : null;
      if (s) { openSent(s); return; }
      closePanel();
    });

    panel.addEventListener("click", function (e) {
      var t = targetOf(e);
      var id = t ? t.id : "";
      if (id === "btn-close") { closePanel(); }
      else if (id === "btn-ins") { genInsent(); }
      else if (id === "btn-reword") { backWord(); }
      else if (id === "btn-add") { vocabAdd(); }
      else if (id === "btn-rm") { vocabRemove(); }
    });

    initTouch();

    post("/api/progress", { book: CFG.book, page: CFG.page, fs: CFG.fs }, function () {});
  }

  window.Reader = { init: init };
})();
