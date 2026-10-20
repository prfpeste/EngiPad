// Pictures (picture("name") lines): the browser side of core/images.py.
//
// Only pure functions here (no DOM), so the same rules can be tested
// against the Python implementation (tests/test_images.py runs both with
// node). The limits come from the server: templates/index.html sets
// window.ENGIPAD_IMAGES from core.images.client_config(), nothing is
// duplicated in this file.
//
// File format (see core/images.py for the full description): the saved
// text ends with an "image section"
//
//     ##!image name
//     <Base64 PNG, 76 characters per line>
//
// which is only recognised as the END of the text. Images are PNG only,
// checked by their bytes (signature, IHDR, IEND) before anything decodes
// them.

(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.EngiPictures = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  let cfg = null;

  function configure(config) {
    cfg = config;
  }

  function config() {
    if (!cfg) throw new Error('EngiPictures is not configured (window.ENGIPAD_IMAGES missing).');
    return cfg;
  }

  const NAME_RE = /^[A-Za-z0-9_]+$/;
  const MARKER_RE = /^##!image[ \t]+([A-Za-z0-9_]+)[ \t]*$/;
  const BASE64_LINE_RE = /^[A-Za-z0-9+/]*={0,2}$/;
  const LINE_WIDTH = 76;

  function isValidName(name) {
    return typeof name === 'string' && name.length >= 1 &&
      name.length <= config().nameMaxLength && NAME_RE.test(name);
  }

  function markerName(line) {
    const match = MARKER_RE.exec(line.replace(/\s+$/, ''));
    if (!match || match[1].length > config().nameMaxLength) return null;
    return match[1];
  }

  function perImageLimit(totalBytes) {
    return Math.floor(totalBytes / config().perImageDivisor);
  }

  function hardPerImageLimit() {
    return perImageLimit(config().hardTotalBytes);
  }

  function decodeBase64(text) {
    if (text.length % 4 !== 0 || !BASE64_LINE_RE.test(text)) {
      throw new Error('damaged image data (not Base64)');
    }
    const binary = atob(text);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function startsWith(bytes, prefix, offset) {
    for (let i = 0; i < prefix.length; i++) {
      if (bytes[(offset || 0) + i] !== prefix[i]) return false;
    }
    return true;
  }

  const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  const PNG_IHDR_HEAD = [0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52];
  const PNG_IEND = [0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82];

  function uint32(bytes, offset) {
    return ((bytes[offset] * 256 + bytes[offset + 1]) * 256 + bytes[offset + 2]) * 256 + bytes[offset + 3];
  }

  // Checks PNG bytes (Uint8Array) and returns {width, height, bytes}.
  // Throws Error with a message for the user.
  function pngInfoFromBytes(bytes, maxBytes) {
    const limit = maxBytes === undefined ? hardPerImageLimit() : maxBytes;
    if (bytes.length > limit) {
      throw new Error('image is too large (' + Math.floor(bytes.length / 1024) +
        ' KB, limit ' + Math.floor(limit / 1024) + ' KB)');
    }
    if (bytes.length < 45 || !startsWith(bytes, PNG_SIGNATURE, 0)) throw new Error('not a PNG image');
    if (!startsWith(bytes, PNG_IHDR_HEAD, 8)) throw new Error('damaged PNG image (no header)');
    if (!startsWith(bytes, PNG_IEND, bytes.length - PNG_IEND.length)) {
      throw new Error('damaged PNG image (incomplete)');
    }

    const width = uint32(bytes, 16);
    const height = uint32(bytes, 20);
    if (width < 1 || height < 1) throw new Error('damaged PNG image (size 0)');
    const c = config();
    if (width > c.maxPixelWidth || height > c.maxPixelHeight || width * height > c.maxPixels) {
      throw new Error('image has too many pixels (' + width + ' x ' + height + '; at most ' +
        c.maxPixelWidth + ' px wide and ' + Math.floor(c.maxPixels / 1000000) + ' million pixels)');
    }
    return { width: width, height: height, bytes: bytes.length };
  }

  // Base64 text -> {width, height, bytes}; throws Error like pngInfoFromBytes().
  function pngInfo(b64, maxBytes) {
    const limit = maxBytes === undefined ? hardPerImageLimit() : maxBytes;
    if (b64.length > Math.floor(limit * 4 / 3) + 16) {
      throw new Error('image is too large (limit ' + Math.floor(limit / 1024) + ' KB)');
    }
    return pngInfoFromBytes(decodeBase64(b64.replace(/\s+/g, '')), maxBytes);
  }

  // Names used in picture("name"...) in any of the codes, first use first.
  // A commented-out line still counts (it must not lose the image).
  function referencedNames(codes) {
    const names = [];
    const re = new RegExp('picture\\(\\s*"([A-Za-z0-9_]{1,' + config().nameMaxLength + '})"', 'g');
    codes.forEach(function (code) {
      re.lastIndex = 0;
      let match;
      while ((match = re.exec(code)) !== null) {
        if (names.indexOf(match[1]) < 0) names.push(match[1]);
      }
    });
    return names;
  }

  function splitTextLines(text) {
    return text.split(/\r\n|\r|\n/);
  }

  // Separates the image section from the end of a text:
  // {code, images: [{name, b64}], warnings: [...]}.
  function splitImageSection(text) {
    const lines = splitTextLines(text);

    let tailStart = lines.length;
    while (tailStart > 0) {
      const line = lines[tailStart - 1];
      const stripped = line.trim();
      if (stripped === '' || markerName(line) !== null || BASE64_LINE_RE.test(stripped)) {
        tailStart--;
      } else {
        break;
      }
    }

    let start = -1;
    for (let k = tailStart; k < lines.length; k++) {
      if (markerName(lines[k]) !== null) { start = k; break; }
    }
    if (start < 0) return { code: text, images: [], warnings: [] };

    const blocks = [];
    for (let k = start; k < lines.length; k++) {
      const name = markerName(lines[k]);
      if (name !== null) blocks.push({ name: name, lines: [] });
      else blocks[blocks.length - 1].lines.push(lines[k].trim());
    }

    const c = config();
    const images = [];
    const warnings = [];
    const seen = {};
    let total = 0;
    blocks.forEach(function (block) {
      try {
        if (seen[block.name]) throw new Error('name used twice');
        if (images.length >= c.maxImages) throw new Error('more than ' + c.maxImages + ' images');
        const b64 = block.lines.join('');
        const info = pngInfo(b64);
        if (total + info.bytes > c.hardTotalBytes) throw new Error('the images are too large altogether');
        seen[block.name] = true;
        total += info.bytes;
        images.push({ name: block.name, b64: b64, width: info.width, height: info.height, bytes: info.bytes });
      } catch (e) {
        warnings.push("Image '" + block.name + "' skipped: " + e.message + '.');
      }
    });

    return { code: lines.slice(0, start).join('\n'), images: images, warnings: warnings };
  }

  // Inverse of splitImageSection(): entries are [{name, b64}].
  function joinImageSection(code, entries) {
    if (!entries.length) return code;
    const parts = entries.map(function (entry) {
      const rows = ['##!image ' + entry.name];
      for (let i = 0; i < entry.b64.length; i += LINE_WIDTH) rows.push(entry.b64.slice(i, i + LINE_WIDTH));
      return rows.join('\n');
    });
    return code + '\n' + parts.join('\n') + '\n';
  }

  return {
    configure: configure,
    isValidName: isValidName,
    perImageLimit: perImageLimit,
    pngInfo: pngInfo,
    pngInfoFromBytes: pngInfoFromBytes,
    referencedNames: referencedNames,
    splitImageSection: splitImageSection,
    joinImageSection: joinImageSection,
  };
}));

if (typeof window !== 'undefined' && window.ENGIPAD_IMAGES && window.EngiPictures) {
  window.EngiPictures.configure(window.ENGIPAD_IMAGES);
}
