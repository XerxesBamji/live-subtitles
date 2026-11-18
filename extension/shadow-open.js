// Runs in the page itself, before the site's own scripts (Disney+ only).
// Disney+ draws its subtitles inside "closed" shadow DOM, which extensions
// can't read – this is likely why other subtitle translators fail there.
// Making new shadow roots "open" lets our overlay read the subtitle text.
(() => {
  const original = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (init) {
    return original.call(this, Object.assign({}, init, { mode: "open" }));
  };
})();
