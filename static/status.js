(function(){
  function paint(sel){
    const v = (sel.value || "novo").toLowerCase();
    sel.className = "status-pick st-" + v;
  }

  document.addEventListener("DOMContentLoaded", ()=>{
    document.querySelectorAll("select[name=status]").forEach(sel=>{
      sel.classList.add("status-pick");
      paint(sel);

      sel.addEventListener("change", ()=>{
        paint(sel);
        fetch(sel.closest("form").action,{
          method:"POST",
          body:new FormData(sel.closest("form")),
          credentials:"same-origin"
        });
      });
    });
  });
})();
