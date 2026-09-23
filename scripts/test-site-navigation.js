"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "../docs/site-navigation.js"),
  "utf8",
);

function makeSite({ mobile, legacyMediaListener = false }) {
  const classes = new Map();
  const documentListeners = {};
  const toggleListeners = {};
  const attributes = new Map([["aria-expanded", "false"]]);
  const link = {};
  const outside = {};
  let onMediaChange;

  const doc = {
    activeElement: null,
    documentElement: {
      classList: {
        toggle(name, present) {
          classes.set(name, present);
        },
      },
    },
    querySelector(selector) {
      assert.equal(selector, 'nav[aria-label="Primary"]');
      return navigation;
    },
    addEventListener(name, listener) {
      documentListeners[name] = listener;
    },
  };
  const toggle = {
    setAttribute(name, value) {
      attributes.set(name, value);
    },
    getAttribute(name) {
      return attributes.get(name);
    },
    addEventListener(name, listener) {
      toggleListeners[name] = listener;
    },
    focus() {
      doc.activeElement = this;
    },
  };
  const links = {
    hidden: false,
    contains(node) {
      return node === link;
    },
  };
  const navigation = {
    querySelector(selector) {
      if (selector === "[data-navigation-toggle]") return toggle;
      if (selector === "#primary-navigation-links") return links;
      throw new Error(`Unexpected selector: ${selector}`);
    },
    contains(node) {
      return node === toggle || node === link;
    },
  };
  const media = { matches: mobile };
  if (legacyMediaListener) {
    media.addListener = (listener) => { onMediaChange = listener; };
  } else {
    media.addEventListener = (name, listener) => {
      assert.equal(name, "change");
      onMediaChange = listener;
    };
  }

  vm.runInNewContext(script, {
    document: doc,
    window: { matchMedia: () => media },
  });
  return {
    classes, doc, documentListeners, link, links, media, navigation, outside,
    toggle, toggleListeners, attributes,
    change: () => onMediaChange(),
  };
}

function testResponsiveFocus() {
  const site = makeSite({ mobile: false });
  assert.equal(site.links.hidden, false);
  assert.equal(site.attributes.get("aria-expanded"), "true");
  assert.equal(site.classes.get("navigation-ready"), false);

  site.doc.activeElement = site.link;
  site.media.matches = true;
  site.change();
  assert.equal(site.links.hidden, true);
  assert.equal(site.attributes.get("aria-expanded"), "false");
  assert.equal(site.classes.get("navigation-ready"), true);
  assert.equal(site.doc.activeElement, site.toggle);

  site.toggleListeners.click();
  assert.equal(site.links.hidden, false);
  site.doc.activeElement = site.link;
  site.media.matches = false;
  site.change();
  assert.equal(site.links.hidden, false);
  assert.equal(site.doc.activeElement, site.link);
}

function testEscapeScope() {
  const site = makeSite({ mobile: true, legacyMediaListener: true });
  assert.equal(site.links.hidden, true);
  site.media.matches = false;
  site.change();
  assert.equal(site.links.hidden, false);
  site.media.matches = true;
  site.change();
  assert.equal(site.links.hidden, true);
  site.toggleListeners.click();
  assert.equal(site.links.hidden, false);

  site.doc.activeElement = site.outside;
  site.documentListeners.keydown({ key: "Escape" });
  assert.equal(site.links.hidden, false);
  assert.equal(site.doc.activeElement, site.outside);

  site.doc.activeElement = site.link;
  site.documentListeners.keydown({ key: "Escape" });
  assert.equal(site.links.hidden, true);
  assert.equal(site.attributes.get("aria-expanded"), "false");
  assert.equal(site.doc.activeElement, site.toggle);
}

testResponsiveFocus();
testEscapeScope();
process.stdout.write("site navigation tests passed\n");
