/* Preserve LaTeX before Markdown consumes its backslashes, then render safely. */
(() => {
  window.renderStudyMarkdown = input => {
    const source=String(input ?? '');
    const escaped=()=>{const node=document.createElement('div');node.textContent=source;return node.innerHTML;};
    if (!window.marked || !window.DOMPurify) return escaped();
    const math=[];
    const nonce='XINGGUIMATH'+crypto.getRandomValues(new Uint32Array(4)).join('')+'N';
    // The first alternatives shield fenced/inline code. Math is never parsed there.
    const pattern=/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|(?<!\\)\$[^$\n]+?\$/g;
    const prepared=source.replace(pattern,match=>{
      if(match.startsWith('`') || match.startsWith('~~~')) return match;
      const display=match.startsWith('\\[') || match.startsWith('$$');
      const length=match.startsWith('$') && !display ? 1 : 2;
      const tex=match.slice(length,-length);
      if(!display && match.startsWith('$') && tex.trim()!==tex) return match;
      const token=nonce+math.length+'END';
      math.push({token,tex,display,original:match});
      return display?'\n\n'+token+'\n\n':token;
    });
    const raw=marked.parse(prepared);
    const safe=DOMPurify.sanitize(raw,{USE_PROFILES:{html:true},
      FORBID_TAGS:['style','form','input','button','iframe','object','embed','img','audio','video','source'],
      FORBID_ATTR:['style','srcset'],ALLOW_DATA_ATTR:false});
    const root=document.createElement('div');root.innerHTML=safe;
    if(!math.length) return root.innerHTML;
    const nodes=[],walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    while(walker.nextNode()) nodes.push(walker.currentNode);
    const tokens=new Map(math.map(item=>[item.token,item]));
    const tokenPattern=new RegExp(nonce+'\\d+END','g');
    for(const node of nodes) {
      if(node.parentElement.closest('pre,code')) continue;
      const text=node.nodeValue;
      tokenPattern.lastIndex=0;
      if(!tokenPattern.test(text)) continue;
      tokenPattern.lastIndex=0;
      const fragment=document.createDocumentFragment();let index=0;
      for(const match of text.matchAll(tokenPattern)) {
        fragment.appendChild(document.createTextNode(text.slice(index,match.index)));
        const item=tokens.get(match[0]);
        const span=document.createElement('span');
        span.className=item.display?'study-math study-math-block':'study-math';
        if(window.katex) {
          try {
            // KaTeX output is generated after Markdown sanitization. Unsafe TeX
            // HTML/URL commands remain disabled, and macro expansion is bounded.
            span.innerHTML=katex.renderToString(item.tex,{displayMode:item.display,throwOnError:false,
              trust:false,strict:'ignore',maxExpand:500,maxSize:20,output:'htmlAndMathml'});
          } catch(error) {span.textContent=item.original;}
        } else span.textContent=item.original;
        fragment.appendChild(span);index=match.index+match[0].length;
      }
      fragment.appendChild(document.createTextNode(text.slice(index)));node.replaceWith(fragment);
    }
    return root.innerHTML;
  };
})();
