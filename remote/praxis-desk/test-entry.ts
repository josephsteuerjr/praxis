import {toolSummary,toolTraceHTML,setResultFetcher} from './desk/ui-kit/steps';
import {mountActionSteps} from './desk/ui-kit/window/panel';
import {phoneActions,currentRunId} from './desk/ui-kit/phone-actions';
import {forgePanel} from './desk/ui-kit/window/views/forge-panel';
import {mountTelegramShell} from './desk/miniapp/src/telegram-shell';
import './desk/ui-kit/phone.css';
Object.assign(window,{stepsTest:{toolSummary,toolTraceHTML,setResultFetcher,mountActionSteps,phoneActions,currentRunId,forgePanel,mountTelegramShell}});
