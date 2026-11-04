"""Forge team schema additions, kept separate from Praxis's authored tool text."""
def extend(tools,obj):
    by_name={t['name']:t for t in tools}
    def properties(name):return by_name[name]['input_schema']['properties']
    properties('coding_session')['max_active_workers']={'type':'integer','description':'Positive active-slot policy shared by this task and every nested agent; default 8. Not a total tree size.'}
    agent=properties('coding_agent')
    agent['action']['enum']+=['disposition','tick','payload','events']
    agent.update({'result_sha256':{'type':'string','description':'Exact hash of the observed result.json bytes.'},
                  'disposition':{'type':'string','enum':['integrated','irrelevant','deferred']},
                  'phase':{'type':'string'},'success_criteria':{'type':'array','items':{'type':'string'}}})
    agent.update({'payload_sha256':{'type':'string'},'kind':{'type':'string','enum':['payload','result','request']},
                  'offset':{'type':'integer'},'limit':{'type':'integer'},'after_seq':{'type':'integer'}})
    by_name['coding_agent']['description']='Spawn/poll/stop/list a real fresh-context team member, record exact-result disposition, or drain the durable queue. payload reads an exact result/request/payload page: pass payload_sha256 (result_sha256 is also accepted for kind=result); follow next_offset until complete. events reads ordered durable events. Parent identity is runtime-owned; queued editing workers share one root. Read-only critics advise; acceptance never relabels their findings.'
    swarm=properties('coding_swarm');swarm['action']['enum']+=['checkpoint','rework']
    swarm['result_sha256']={'type':'string'}
    by_name['coding_swarm']['description']='Durable dependency graph, exact-result acceptance and rework. plan nodes include phase/success_criteria; dependencies require the exact accepted result revision. For action=checkpoint put a JSON object with next_action/unresolved_reviews in plan (message accepts the same object for compatibility). rework needs node_id, current result_sha256 and concrete correction message. Ownership is advisory.'
    properties('coding_inspect')['action']['enum']+=['coordination']
    tools.extend([
        {'name':'coding_control','description':'Pause/Stop/Resume a task or selected agent subtree. Resume needs current generation and proven old-writer exit, creates a new identity and retains history. Unknown outcome is not stopped.',
         'input_schema':obj({'task_id':{'type':'string'},'action':{'type':'string','enum':['pause','stop','resume']},
            'agent_id':{'type':'string'},'expected_generation':{'type':'string'}},['task_id','action'])},
        {'name':'coding_review','description':'Prepare/start/inspect independent frozen source review or a separate isolated A/L/B/M merger, retain findings, request review of fixes and record an explicit author decision. A=base,L=authored local,B=incoming,M=candidate. This never applies live code; critics remain advisory.',
         'input_schema':obj({'task_id':{'type':'string'},'action':{'type':'string','enum':['prepare','start','status','decide','prepare_merge','start_merge','rework_merge','verification_plan','bind_verification']},
            'review_id':{'type':'string'},'criteria':{'type':'string'},'previous_review_id':{'type':'string'},'merge_id':{'type':'string'},
            'paths':{'type':'array','items':{'type':'string'}},'base':{'type':'string'},'local':{'type':'string'},'incoming':{'type':'string'},
            'critics':{'type':'integer'},'max_iters':{'type':'integer'},'model':{'type':'string'},
            'expected_subject_sha256':{'type':'string'},'decision':{'type':'string','enum':['accept','defer','reject','override']},'reason':{'type':'string'}},['task_id','action'])}
    ])
    descriptions={
        'task_id':'Durable Forge task identity.','action':'Requested typed operation.',
        'agent_id':'Selected participant identity; empty selects the task.',
        'expected_generation':'Generation observed in the latest participant progress; stale control is refused.',
        'disposition':'Author decision about this exact result; independent findings retain their own verdict.',
        'phase':'Concrete stage of the assigned work.','success_criteria':'Observable conditions for completing this assignment.',
        'result_sha256':'SHA-256 of the exact observed result.json bytes, not a Git or subject hash.',
        'review_id':'Identity returned by the frozen review or merge preparation.',
        'criteria':'Concrete review criteria or correction brief.',
        'previous_review_id':'Previous review whose findings the new revision must address.',
        'merge_id':'Finished isolated merger whose exact A/L/B/M composition should be reviewed.',
        'paths':'Optional exact relative source paths; the recorded scope says when review is partial.',
        'base':'Directory of original base A.','local':'Directory of authored local source L.',
        'incoming':'Directory of incoming source B.','critics':'Explicit independent critic count, from 1 through 8; default 1.',
        'max_iters':'Per-unit model turn allowance; zero retains the configured Forge default.',
        'model':'Optional configured model; empty retains the voice-role route.',
        'expected_subject_sha256':'Exact frozen review subject hash returned by preparation.',
        'decision':'Explicit author disposition; override never changes the critic verdict.',
        'reason':'Concrete reason for the author decision.'}
    descriptions.update({'payload_sha256':'Exact SHA of the full immutable payload or result/request record.',
        'kind':'Record type to page: immutable payload or exact result/request JSON.',
        'offset':'Unicode character offset returned by the previous content page.',
        'limit':'Maximum page length; outputs expose the next offset explicitly.',
        'after_seq':'Last consumed event sequence, for ordered journal paging.'})
    review_schema=next(t for t in tools if t['name']=='coding_review')['input_schema']['properties']
    review_schema.update({'verification_id':{'type':'string'},'expected_candidate_sha256':{'type':'string'}})
    descriptions.update({'verification_id':'Actual canonical verification matrix identity from the exported stand.',
        'expected_candidate_sha256':'Exact planned source composition SHA; distinct from review subject and result hashes.'})
    for tool in tools:
        if tool['name'] not in {'coding_agent','coding_swarm','coding_control','coding_review'}:continue
        for key,value in tool['input_schema']['properties'].items():
            if not value.get('description') and key in descriptions:value['description']=descriptions[key]
