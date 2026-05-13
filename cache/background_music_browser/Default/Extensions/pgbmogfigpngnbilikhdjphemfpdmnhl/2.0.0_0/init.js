const regExp = /(?=.*sidebar)(?=.*naver-webtoon)/;
const ua = navigator.userAgent;

if (regExp.test(ua)) {
    document.documentElement.classList.add("_naver_webtoon_extension");
}
