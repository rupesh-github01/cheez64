'use client';
import {useEffect,useState} from 'react';
import {listGames} from '@/lib/api';
import type {Game} from '@/lib/types';
import {PAGE_SIZE,type StatusFilter,type ResultFilter} from './library-presentation';
type Snapshot={key:string;games:Game[];total:number|null;loading:boolean;error:string|null};
export function useGameLibrary(status:StatusFilter,result:ResultFilter,offset:number,revision:number){
  const key=JSON.stringify([status,result,offset,revision]);
  const [snapshot,setSnapshot]=useState<Snapshot>({key:'',games:[],total:null,loading:true,error:null});
  useEffect(()=>{
    let current=true;
    const params={limit:PAGE_SIZE,offset,...(status?{analysis_status:status}:{}),...(result?{result}: {})};
    listGames(params).then(response=>{
      if(!Array.isArray(response.games)||!Number.isInteger(response.total)||response.total<0||response.games.length>PAGE_SIZE)throw new Error('The library returned an unreadable response.');
      if(current)setSnapshot({key,games:response.games,total:response.total,loading:false,error:null});
    }).catch((error:unknown)=>{if(current)setSnapshot({key,games:[],total:null,loading:false,error:error instanceof Error?error.message:'The library could not be reached.'});});
    return()=>{current=false;};
  },[key,status,result,offset]);
  // Hide a previous query immediately, before the next effect/request starts.
  return snapshot.key===key?snapshot:{key,games:[],total:null,loading:true,error:null};
}
