var port = {};

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
	if (message.type === "contentScriptLoaded") {
		sendResponse({ acknowledged: true });
	}
	return true;
});

chrome.runtime.onMessageExternal.addListener(
	function (request, sender, sendResponse) {
		if (request.svc == "install") {
			sendResponse("install");
			return;
		}

		if (request.svc == undefined || request.ver == undefined || request.param == undefined) {
			sendResponse("param");
			return;
		}

		var param = request.param;
		var svc = request.svc;
		var ver = request.ver;
		param.tabId = sender.tab.id;
		param.svc = svc;
		param.ver = ver;

		var hostName = svc + "_" + ver;

		if (port[svc] == undefined) {
			chrome.runtime.sendNativeMessage(hostName,
				{},
				function (response) {
					if (response == undefined) {
						// proxy app not installed
						error = {};
						error.err = true;
						error.errCode = 1000;
						error.tabId = param.tabId;
						sendWebMessage(error);
					} else {
						// proxy app installed
						executeProxy(param);
						sendNativeMessage(param);
					}
				});
		} else {
			sendNativeMessage(param);
		}
	}
);

function onNativeMessage(msg) {
	if (msg == undefined || msg == null) {
		return;
	}

	sendWebMessage(msg);
}

function sendWebMessage(msg) {
	if (msg.tabId === undefined || msg.tabId === null) {
		console.warn("[ext] not found tabId in message.");
		return;
	}

	const tabId = msg.tabId;
	delete msg.tabId;

	chrome.tabs.get(parseInt(tabId), function (tab) {
		if (chrome.runtime.lastError) {
			console.error("[ext] failed tab query:", chrome.runtime.lastError.message);
			return;
		}

		const data = JSON.stringify(msg)
		chrome.tabs.sendMessage(parseInt(tabId), data, function (response) {
			if (chrome.runtime.lastError) {
				return;
			}
		});
	});
}

function onDisconnect(msg) {
	if (msg.name != undefined && msg.name != null) {
		delete port[msg.name];
	}
}

function sendNativeMessage(param) {
	data = {
		'param': param
	}

	port[param.svc].postMessage(data);
}

function executeProxy(param) {
	var svc = param.svc;

	if (port[svc] == undefined) {
		var hostName = svc + "_" + param.ver;
		port[svc] = chrome.runtime.connectNative(hostName);
		port[svc].onMessage.addListener(onNativeMessage);
		port[svc].onDisconnect.addListener(onDisconnect);
		port[svc].name = svc;
	}
}

chrome.tabs.onRemoved.addListener(function callback(tabId, removeInfo) {
	for (let svc in port) {
		if (typeof port[svc].disconnect === 'function') {
			port[svc].disconnect();
			delete port[svc];
		}
	}
})

