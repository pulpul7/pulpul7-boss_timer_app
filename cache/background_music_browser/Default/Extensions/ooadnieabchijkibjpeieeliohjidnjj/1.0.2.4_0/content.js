try {
	chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
		sendResponse({
			success: true,
			received: true
		});

		try {
			if (typeof passToWeb === 'function') {
				passToWeb(message);
			} else {
				console.warn("not found target function.");
			}
		} catch (error) {
			console.error("error occur send message to content.", error);
		}
	});
} catch (error) {
	console.error("failed add event listener.", error);
}

function passToWeb(data) {
	if (typeof window === 'undefined' || !window.postMessage) {
		console.error("not define postMessage function.");
		return;
	}
	window.postMessage(data, '*');
}
