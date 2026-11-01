"""Collect rendered relationship geometry for browser acceptance tests."""


RELATION_GEOMETRY_SCRIPT = """() => {
  const cards=[...document.querySelectorAll('.card')].map(card=>{
    const box=card.querySelector('.card-shape').getBBox();
    const matrix=card.transform.baseVal.consolidate().matrix;
    return {id:card.dataset.nodeId,left:matrix.e+box.x,
      right:matrix.e+box.x+box.width,top:matrix.f+box.y,
      bottom:matrix.f+box.y+box.height};
  });
  const cardsById=new Map(cards.map(card=>[card.id,card]));
  const segmentHitsCard=(start,end,card)=>{
    for(let step=1;step<1000;step++){
      const amount=step/1000;
      const x=start.x+(end.x-start.x)*amount;
      const y=start.y+(end.y-start.y)*amount;
      if(x>card.left&&x<card.right&&y>card.top&&y<card.bottom)return true;
    }
    return false;
  };
  return [...document.querySelectorAll('.relation-edge')].map(edge=>{
    const source=edge.dataset.source,target=edge.dataset.target;
    const endpointIds=new Set([source,target]);
    const obstacles=cards.filter(card=>!endpointIds.has(card.id));
    const length=edge.getTotalLength(),collisions=new Set();
    for(let step=1;step<1000;step++){
      const point=edge.getPointAtLength(length*step/1000);
      for(const card of obstacles)if(point.x>card.left&&point.x<card.right&&
        point.y>card.top&&point.y<card.bottom)collisions.add(card.id);
    }
    const sourceCard=cardsById.get(source),targetCard=cardsById.get(target);
    let intermediateCards=[];
    if(sourceCard&&targetCard){
      const start={x:(sourceCard.left+sourceCard.right)/2,
        y:(sourceCard.top+sourceCard.bottom)/2};
      const end={x:(targetCard.left+targetCard.right)/2,
        y:(targetCard.top+targetCard.bottom)/2};
      intermediateCards=obstacles.filter(card=>
        segmentHitsCard(start,end,card)).map(card=>card.id);
    }
    return {source,target,collisions:[...collisions],intermediateCards};
  });
}"""


def collect_relation_geometry(page):
    """Return each relationship path, its card collisions, and crossed cards."""
    return page.evaluate(RELATION_GEOMETRY_SCRIPT)
