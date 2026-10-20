"""Trading endpoints share JARVIS's local session and same-origin protections."""
import asyncio
from fastapi.responses import Response
from .analytics import export_csv, export_pdf
from .commands import execution_request


def register(app,service,assistant=None):
    @app.get('/api/trading')
    async def snapshot():
        return await asyncio.to_thread(service.snapshot)

    @app.post('/api/trading/control')
    async def control(body:dict):
        if set(body)-{'action','mode','close_positions'}: raise ValueError('Unsupported trading control fields')
        if 'close_positions' in body and type(body['close_positions']) is not bool: raise ValueError('close_positions must be a boolean')
        args=(body.get('action'),body.get('mode'),body.get('close_positions',False))
        stopping=body.get('action') in {'emergency','pause_today'} or (body.get('action')=='mode' and body.get('mode')=='OFF')
        if stopping and assistant:
            for cid in list(assistant.active):
                context=assistant.contexts.get(cid)
                if context and execution_request(context.request): assistant.cancel(cid)
        if body.get('action')=='connect': return await asyncio.to_thread(service.control,*args)
        return service.control(*args)

    @app.patch('/api/trading/config')
    async def configure(body:dict):
        return await asyncio.to_thread(service.update_config,body)

    @app.post('/api/trading/data')
    async def data(body:dict):
        return await asyncio.to_thread(service.ingest,body)

    @app.post('/api/trading/backtests')
    async def backtest(body:dict):
        return await asyncio.to_thread(service.backtest,body)

    @app.get('/api/trading/backtests/{run_id}')
    async def report(run_id:str):
        return await asyncio.to_thread(service.run_report,run_id)

    @app.get('/api/trading/journal')
    async def journal(dataset:str='paper',query:str='',start:str=None,end:str=None,run_id:str=None):
        return await asyncio.to_thread(service.journal,dataset,query,start,end,run_id)

    @app.get('/api/trading/export')
    async def export(dataset:str='paper',format:str='csv',run_id:str=None,start:str=None,end:str=None,query:str=''):
        report=await asyncio.to_thread(service.journal,dataset,query,start,end,run_id)
        if format=='csv':
            return Response(export_csv(report['trades']),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="JARVIS-'+dataset+'-journal.csv"'})
        if format=='pdf':
            return Response(export_pdf(report),media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="JARVIS-'+dataset+'-report.pdf"'})
        raise ValueError('Choose CSV or PDF export')

    @app.post('/api/trading/research')
    async def research(body:dict):
        return await asyncio.to_thread(service.research,body)

    @app.post('/api/trading/strategies')
    async def propose(body:dict):
        if set(body)-{'name','changes'}: raise ValueError('Unsupported strategy proposal fields')
        return await asyncio.to_thread(service.propose,body.get('name','New strategy proposal'),body.get('changes',{}))

    @app.post('/api/trading/strategies/{version}/activate')
    async def activate(version:str,body:dict):
        if set(body)-{'approved'}: raise ValueError('Unsupported activation fields')
        return await asyncio.to_thread(service.activate,version,body.get('approved',False))
