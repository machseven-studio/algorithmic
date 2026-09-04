/* Algorithmic navigation repair: keep existing module markup intact. */
(function(){
'use strict';
function ready(){
 const sidebar=document.querySelector('#moduleSidebar');
 if(!sidebar)return;
 const groups=document.querySelector('#moduleGroups')||sidebar;
 document.querySelectorAll('#algorithmic-final-nav,#algorithmic_final_nav').forEach(e=>e.remove());
 let home=groups.querySelector('[data-head="homepage"], [data-module-head="homepage"], .module-head-group[data-head-group="homepage"]');
 if(!home){
   const candidates=[...groups.querySelectorAll('button,div')].filter(e=>/homepage/i.test((e.textContent||'').trim()));
   home=candidates[0]?.closest('.module-head-group')||candidates[0];
 }
 if(home){
   const arrow=home.querySelector('.module-head-arrow,.head-arrow,[data-module-arrow]');
   if(!arrow){
     const b=document.createElement('button'); b.type='button'; b.className='module-head-arrow'; b.textContent='⌄'; b.setAttribute('aria-label','Toggle Homepage');
     b.style.cssText='margin-left:auto;background:none;border:0;color:#aaa;padding:4px 8px;cursor:pointer;font-size:14px;';
     home.appendChild(b);
   }
 }
 sidebar.addEventListener('click',function(e){
   const arrow=e.target.closest('.module-head-arrow,.head-arrow,[data-module-arrow]');
   if(!arrow)return;
   e.preventDefault(); e.stopPropagation();
   const group=arrow.closest('.module-head-group,[data-head-group],.module-group');
   if(!group)return;
   const child=group.querySelector(':scope > div[id^="head-"],:scope > .module-children,:scope > .children');
   if(child)child.classList.toggle('hidden');
   arrow.textContent=child&&!child.classList.contains('hidden')?'⌃':'⌄';
 },true);
 let toggle=document.querySelector('#sidebarCollapseButton');
 if(!toggle){toggle=document.createElement('button');toggle.id='sidebarCollapseButton';sidebar.appendChild(toggle);}
 toggle.type='button'; toggle.textContent='‹';
 Object.assign(toggle.style,{position:'absolute',top:'10px',right:'-14px',zIndex:'9999',width:'28px',height:'28px',borderRadius:'50%',background:'#0b0b0b',color:'#d4af37',border:'1px solid rgba(212,175,55,.5)',cursor:'pointer'});
 toggle.onclick=function(){const c=sidebar.classList.toggle('algorithmic-sidebar-collapsed');toggle.textContent=c?'›':'‹';};
 const style=document.createElement('style'); style.id='algorithmic-nav-style'; style.textContent=`
 #moduleSidebar{position:relative;transition:width .2s ease,min-width .2s ease!important;overflow:visible!important}
 #moduleSidebar.algorithmic-sidebar-collapsed{width:0!important;min-width:0!important;padding:0!important;border:0!important;overflow:visible!important}
 #moduleSidebar.algorithmic-sidebar-collapsed>*:not(#sidebarCollapseButton){display:none!important}
 #moduleSidebar.algorithmic-sidebar-collapsed #sidebarCollapseButton{display:block!important;right:-14px!important}
 #moduleGroups{display:flex!important;flex-direction:column!important;align-items:stretch!important}
 .module-head-group{width:100%!important}
 `; document.head.appendChild(style);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready);else ready();
})();
