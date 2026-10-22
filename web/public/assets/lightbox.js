/* 写真をクリックで拡大表示（オーバーレイ）。対象：.spotphoto img / .photocand img。背景・×・Escで閉じる */
(function () {
  var imgs = document.querySelectorAll('.spotphoto img, .photocand img');
  if (!imgs.length) return;
  var box = null;
  function close() { if (box) { document.body.removeChild(box); box = null; document.removeEventListener('keydown', onKey); } }
  function onKey(e) { if (e.key === 'Escape' || e.keyCode === 27) close(); }
  function open(img) {
    close();
    box = document.createElement('div');
    box.className = 'lightbox';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', '写真の拡大表示');
    var big = document.createElement('img');
    big.src = img.currentSrc || img.src;
    big.alt = img.alt || '';
    big.referrerPolicy = 'no-referrer';
    var cap = img.closest('figure, .photocand');
    var credit = null;
    if (cap) {
      var cs = cap.querySelectorAll('figcaption, .small.muted');
      for (var i = 0; i < cs.length; i++) { if ((cs[i].textContent || '').replace(/^\s+/, '').indexOf('写真') === 0) { credit = cs[i]; break; } }
    }
    var fig = document.createElement('figure');
    fig.appendChild(big);
    if (credit) { var c = document.createElement('figcaption'); c.innerHTML = credit.innerHTML; fig.appendChild(c); }
    var x = document.createElement('button');
    x.type = 'button'; x.className = 'lightbox-close'; x.setAttribute('aria-label', '閉じる'); x.textContent = '×';
    box.appendChild(fig); box.appendChild(x);
    box.addEventListener('click', function (e) { if (e.target === box || e.target === x) close(); });
    document.body.appendChild(box);
    document.addEventListener('keydown', onKey);
  }
  Array.prototype.forEach.call(imgs, function (img) {
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', function () { open(img); });
  });
})();
