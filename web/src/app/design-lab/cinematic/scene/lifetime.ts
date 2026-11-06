/** One cancellable RAF owner; independently testable without a browser/GPU. */
export function frameOwner(request:(callback:FrameRequestCallback)=>number,cancel:(id:number)=>void,frame:(time:number)=>boolean) {
  let id:number|null=null,disposed=false;
  const tick:FrameRequestCallback=time=>{id=null;if(!disposed&&frame(time))wake();};
  function wake(){if(!disposed&&id===null)id=request(tick);}
  function pause(){if(id!==null)cancel(id);id=null;}
  return {wake,pause,dispose(){disposed=true;pause();},get pending(){return id!==null;}};
}
