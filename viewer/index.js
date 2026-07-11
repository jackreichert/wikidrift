<script>(function(){
var q=document.getElementById('q'),sortSel=document.getElementById('sort'),cat='all',page=1,PER=25;
var box=document.querySelector('.findings'),pager=document.getElementById('pager');
var rows=[].slice.call(box.querySelectorAll('.finding'));
var countEl=document.getElementById('count');
function num(r){return parseFloat(r.dataset.score)||0;}
function cmp(a,b){var s=sortSel.value;
if(s==='div')return num(b)-num(a)||a.dataset.title.localeCompare(b.dataset.title);
if(s==='cat')return a.dataset.cat.localeCompare(b.dataset.cat)||a.dataset.title.localeCompare(b.dataset.title);
return a.dataset.title.localeCompare(b.dataset.title);}
function drawPager(pages){if(pages<=1){pager.innerHTML='';return;}
pager.innerHTML='<button type="button" class="pg" id="prev"'+(page<=1?' disabled':'')+'>\u2190 Prev</button>'
+'<span class="pgn">Page '+page+' of '+pages+'</span>'
+'<button type="button" class="pg" id="next"'+(page>=pages?' disabled':'')+'>Next \u2192</button>';
var pv=document.getElementById('prev'),nx=document.getElementById('next');
if(pv)pv.onclick=function(){if(page>1){page--;apply();}};
if(nx)nx.onclick=function(){page++;apply();};}
function apply(){var s=(q.value||'').toLowerCase();
var m=rows.filter(function(r){return (cat==='all'||r.dataset.cat===cat)&&r.dataset.text.indexOf(s)>-1;});
m.sort(cmp);m.forEach(function(r){box.appendChild(r);});
rows.forEach(function(r){r.hidden=true;});
var pages=Math.max(1,Math.ceil(m.length/PER));if(page>pages)page=pages;
m.forEach(function(r,i){r.hidden=(i<(page-1)*PER||i>=page*PER);});
if(countEl){
  countEl.textContent=m.length===0?'No matches':(m.length+(m.length===1?' finding':' findings'));
}
var empty=document.getElementById('empty');
if(empty)empty.hidden=m.length>0;
drawPager(pages);}
function reset(){page=1;apply();}
if(q)q.addEventListener('input',reset);
if(sortSel)sortSel.addEventListener('change',reset);
document.querySelectorAll('.fchip').forEach(function(b){b.onclick=function(){cat=b.dataset.cat;
document.querySelectorAll('.fchip').forEach(function(x){var on=x===b;x.classList.toggle('active',on);
x.setAttribute('aria-pressed',on);});reset();};});
apply();})();</script>
