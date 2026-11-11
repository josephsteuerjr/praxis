import {toolSummary,toolTraceHTML,setResultFetcher} from '../ui-kit/steps';
import {mountActionSteps} from '../ui-kit/window/panel';
import {phoneActions,currentRunId} from '../ui-kit/phone-actions';
import {forgePanel} from '../ui-kit/window/views/forge-panel';
import {mountTelegramShell} from '../miniapp/src/telegram-shell';
import '../ui-kit/phone.css';
Object.assign(window,{stepsTest:{toolSummary,toolTraceHTML,setResultFetcher,mountActionSteps,phoneActions,currentRunId,forgePanel,mountTelegramShell}});
