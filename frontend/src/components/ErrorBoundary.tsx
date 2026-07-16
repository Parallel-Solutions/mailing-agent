import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Result } from 'antd';

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('React error boundary', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <Result
          status="error"
          title="Произошла ошибка интерфейса"
          subTitle={this.state.error.message}
          extra={
            <Button type="primary" onClick={() => window.location.assign('/')}>
              На главную
            </Button>
          }
        />
      );
    }
    return this.props.children;
  }
}
