"""Portable source-native CLI. Reads never fetch, ingest or promote releases."""
import argparse
import json
import os
import sys
import sqlite3
import subprocess
from .config import open_reader
from .query import Query
from .limits import deadline

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default=os.environ.get('GITRX_CONFIG'))
    p.add_argument('--backend',choices=['sqlite','git','rebuild']);p.add_argument('--timeout-seconds',type=float,default=10)
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('verify-citation');c.add_argument('setid');c.add_argument('version',type=int);c.add_argument('--raw-sha256',required=True);c.add_argument('--snapshot',required=True);c.add_argument('--ordinal',type=int);c.add_argument('--quote')
    c=sub.add_parser('installation-plan');c.add_argument('setids',nargs='+')
    c=sub.add_parser('inventory-on-date');c.add_argument('on_date');c.add_argument('setids',nargs='*');c.add_argument('--all-families',action='store_true');c.add_argument('--limit',type=int,default=50);c.add_argument('--offset',type=int,default=0)
    c=sub.add_parser('token-provenance');c.add_argument('setid');c.add_argument('version',type=int);c.add_argument('ordinal',type=int);c.add_argument('--limit',type=int,default=100);c.add_argument('--offset',type=int,default=0)
    sub.add_parser('describe-release')
    f=sub.add_parser('families');f.add_argument('--search',default='');f.add_argument('--limit',type=int,default=25);f.add_argument('--offset',type=int,default=0)
    r=sub.add_parser('resolve');r.add_argument('family')
    v=sub.add_parser('versions');v.add_argument('setid');v.add_argument('--limit',type=int,default=200);v.add_argument('--offset',type=int,default=0)
    s=sub.add_parser('show');s.add_argument('setid');s.add_argument('version',type=int,nargs='?');s.add_argument('--date',dest='on_date');s.add_argument('--code');s.add_argument('--ordinal',type=int);s.add_argument('--max-chars',type=int,default=16000);s.add_argument('--offset',type=int,default=0);s.add_argument('--section-limit',type=int,default=100);s.add_argument('--section-offset',type=int,default=0)
    d=sub.add_parser('diff');d.add_argument('setid');d.add_argument('a',type=int);d.add_argument('b',type=int);d.add_argument('--ordinal',type=int);d.add_argument('--code');d.add_argument('--max-chars',type=int,default=16000);d.add_argument('--limit',type=int,default=50);d.add_argument('--offset',type=int,default=0)
    s=sub.add_parser('search');s.add_argument('--time-budget-seconds',type=float,default=2);s.add_argument('--cursor');s.add_argument('phrase');s.add_argument('--limit',type=int,default=25);s.add_argument('--family-offset',type=int,default=0);s.add_argument('--max-families',type=int,default=128);s.add_argument('--section-offset',type=int,default=0)
    f=sub.add_parser('when-added',aliases=['first-observed']);f.add_argument('setid');f.add_argument('phrase');f.add_argument('--code');f.add_argument('--case-sensitive',action='store_true');f.add_argument('--max-versions',type=int,default=500)
    h=sub.add_parser('phrase-history');h.add_argument('setid');h.add_argument('phrase');h.add_argument('--code');h.add_argument('--case-sensitive',action='store_true');h.add_argument('--limit',type=int,default=50);h.add_argument('--offset',type=int,default=0);h.add_argument('--cursor')
    d=sub.add_parser('version-changes');d.add_argument('setid');d.add_argument('version',type=int);d.add_argument('--previous',type=int);d.add_argument('--code');d.add_argument('--limit',type=int,default=50);d.add_argument('--offset',type=int,default=0);d.add_argument('--max-chars',type=int,default=16000)
    d=sub.add_parser('search-changes');d.add_argument('setid');d.add_argument('phrase');d.add_argument('--code');d.add_argument('--case-sensitive',action='store_true');d.add_argument('--start');d.add_argument('--end');d.add_argument('--limit',type=int,default=25);d.add_argument('--cursor');d.add_argument('--max-transitions',type=int,default=50)
    c=sub.add_parser('change-records');c.add_argument('setid');c.add_argument('to_version',type=int,nargs='?');c.add_argument('--from-version',type=int);c.add_argument('--group',choices=['BW','C','WP','AR','DI','USP','MG']);c.add_argument('--limit',type=int,default=25);c.add_argument('--offset',type=int,default=0);c.add_argument('--max-chars',type=int,default=16000)
    c=sub.add_parser('coverage');c.add_argument('setid')
    h=sub.add_parser('section-history');h.add_argument('setid');h.add_argument('--code',required=True);h.add_argument('--max-versions',type=int,default=500);h.add_argument('--offset',type=int,default=0)
    args=vars(p.parse_args(argv));config=args.pop('config');backend=args.pop('backend');command=args.pop('command');timeout=args.pop('timeout_seconds')
    if command=='describe-release':command='describe_release'
    if command in ('verify-citation','installation-plan','inventory-on-date','token-provenance'):command=command.replace('-','_')
    if command=='inventory_on_date' and not args['setids']:args['setids']=None
    if 'setid' in args:args['sid']=args.pop('setid')
    if command=='version-changes':command='version_changes'
    if command=='search-changes':
        command='search_changes';args['ignore_case']=not args.pop('case_sensitive')
    if command=='phrase-history':
        command='phrase_history';args['ignore_case']=not args.pop('case_sensitive')
    if command=='section-history':command='section_history'
    if command=='change-records':command='change_records'
    if command=='show':
        on_date=args.pop('on_date')
        if (on_date is None)==(args['version'] is None):p.error('Choose one version or --date YYYY-MM-DD')
        if on_date is not None:command='show_date';args.pop('version');args['on_date']=on_date
    if command=='diff':
        ordinal=args.pop('ordinal')
        if ordinal is None:command='compare'
        else:
            if args['code'] is not None:p.error('Choose --ordinal or --code')
            args.pop('code');args.pop('limit');args.pop('offset');args['ordinal']=ordinal
    if command in ('when-added','first-observed'):
        command='first_observed';args['ignore_case']=not args.pop('case_sensitive')
    if not config:p.error('Set --config or GITRX_CONFIG; no workspace path is assumed')
    try:
        with deadline(timeout), open_reader(config,backend) as reader:
            print(json.dumps(getattr(Query(reader),command)(timeout_seconds=timeout,**args),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,OSError,RuntimeError,sqlite3.Error,subprocess.SubprocessError) as exc:
        print(json.dumps({'error':type(exc).__name__,'message':str(exc),'evidence_answer':False}),file=sys.stderr);return 2
    return 0

if __name__=='__main__':sys.exit(main())
