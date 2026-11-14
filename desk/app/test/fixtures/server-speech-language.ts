import {setLocale} from '../../../lang/lang';
import {cfg} from '../../../ui-kit/window/api';
import {S} from '../../../ui-kit/window/state';
import {serverSpeechCard} from '../../../ui-kit/window/server-speech';
setLocale(new URL(location.href).searchParams.get('lang')||'en');
Object.assign(cfg,{base:'',key:'fixture',agent:'fixture'});S.view='settings';
document.body.append(serverSpeechCard());
