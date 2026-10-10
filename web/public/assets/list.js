// ロケハンリスト：気になる地点をこのブラウザに貯めておき、一覧・地図・CSV・共有URLにする
// 保存先はこのブラウザだけ（ログイン不要）。共有は list.php?ids=… のURLで行う
(function () {
  const KEY = 'locadb_list';
  const read = () => { try { return JSON.parse(localStorage.getItem(KEY) || '[]').map(Number).filter(Boolean); } catch (e) { return []; } };
  const write = ids => { try { localStorage.setItem(KEY, JSON.stringify(ids)); } catch (e) {} paint(); };
  window.locadbList = { read, write };

  function paint() {
    const ids = read();
    document.querySelectorAll('a.addlist').forEach(a => {
      const on = ids.includes(Number(a.dataset.id));
      a.classList.toggle('on', on);
      if (!a.dataset.label) a.dataset.label = a.textContent;
      a.textContent = on ? '✓ リストに追加済み' : a.dataset.label;
    });
    document.querySelectorAll('.mainnav a[href="list.php"]').forEach(a => {
      let b = a.querySelector('.count');
      if (!b) { b = document.createElement('span'); b.className = 'count'; a.appendChild(b); }
      b.textContent = ids.length ? String(ids.length) : '';
      a.href = ids.length ? 'list.php?ids=' + ids.join(',') : 'list.php';
    });
  }
  document.addEventListener('click', e => {
    const a = e.target.closest('a.addlist');
    if (!a) return;
    e.preventDefault();
    const id = Number(a.dataset.id), ids = read();
    write(ids.includes(id) ? ids.filter(x => x !== id) : ids.concat(id));
  });
  paint();
})();
