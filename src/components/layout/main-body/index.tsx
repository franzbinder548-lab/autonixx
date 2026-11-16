import { useEffect } from 'react';
import { observer } from 'mobx-react-lite';
import { useStore } from '@/hooks/useStore';
import { useDevice } from '@deriv-com/ui';
import './main-body.scss';

type TMainBodyProps = {
    children: React.ReactNode;
};

const MainBody: React.FC<TMainBodyProps> = observer(({ children }) => {
    const { ui } = useStore() ?? {
        ui: {
            setDevice: () => {},
            setDarkMode: () => {},
            is_dark_mode_on: false,
        },
    };
    const { setDevice, setDarkMode, is_dark_mode_on } = ui;
    const { isDesktop, isMobile, isTablet } = useDevice();

    // Keep the trading app aligned with Autonix's shared dark theme.
    useEffect(() => {
        const body = document.querySelector('body');
        if (!body) return;
        document.documentElement.setAttribute('data-theme', 'dark');
        body.classList.remove('theme--light');
        body.classList.add('theme--dark');
        if (!is_dark_mode_on) setDarkMode(true);
    }, [is_dark_mode_on, setDarkMode]);

    useEffect(() => {
        if (isMobile) {
            setDevice('mobile');
        } else if (isTablet) {
            setDevice('tablet');
        } else {
            setDevice('desktop');
        }
    }, [isDesktop, isMobile, isTablet, setDevice]);

    return <div className='main-body'>{children}</div>;
});

export default MainBody;
